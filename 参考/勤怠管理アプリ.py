import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import calendar
from datetime import datetime, timedelta

# --- 設定・定数 ---
DB_NAME = 'work_schedule.db'
WEEK_DAYS = ['日', '月', '火', '水', '木', '金', '土']

# --- データベース管理クラス ---
class DatabaseManager:
    def __init__(self, db_name):
        self.db_name = db_name
        self.needs_pay_backfill = False
        self.init_db()

    def init_db(self):
        """データベースとテーブルの初期化"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # シフト・実績テーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                date TEXT PRIMARY KEY,
                start_plan TEXT,
                end_plan TEXT,
                start_actual TEXT,
                end_actual TEXT,
                break_min INTEGER DEFAULT 0,
                is_holiday INTEGER DEFAULT 0,
                memo TEXT,
                daily_pay INTEGER DEFAULT 0
            )
        ''')

        # 既存DBに日別支給額カラムがない場合は追加
        cursor.execute("PRAGMA table_info(shifts)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'daily_pay' not in columns:
            cursor.execute("ALTER TABLE shifts ADD COLUMN daily_pay INTEGER DEFAULT 0")
            self.needs_pay_backfill = True
        else:
            cursor.execute("SELECT COUNT(*) FROM shifts WHERE daily_pay IS NULL")
            if cursor.fetchone()[0] > 0:
                self.needs_pay_backfill = True
        
        # 設定テーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # デフォルト設定の投入（なければ）
        default_settings = {
            'hourly_wage': '1000',
            'night_rate': '1.25',  # 深夜割増倍率
            'holiday_add': '0'     # 休日手当（時給にプラス円）
        }
        for k, v in default_settings.items():
            cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))
            
        conn.commit()
        conn.close()

    def get_setting(self, key):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def update_setting(self, key, value):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
        conn.close()

    def upsert_shift(self, date, s_plan, e_plan, s_act, e_act, brk, is_hol, memo, daily_pay=0):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO shifts 
            (date, start_plan, end_plan, start_actual, end_actual, break_min, is_holiday, memo, daily_pay)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (date, s_plan, e_plan, s_act, e_act, brk, is_hol, memo, daily_pay))
        conn.commit()
        conn.close()

    def get_shift(self, date):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM shifts WHERE date = ?', (date,))
        row = cursor.fetchone()
        conn.close()
        return row

    def get_shifts_by_range(self, start_date, end_date):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM shifts WHERE date BETWEEN ? AND ? ORDER BY date', (start_date, end_date))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_all_shifts(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM shifts')
        rows = cursor.fetchall()
        conn.close()
        return rows

    def update_daily_pays(self, pay_by_date):
        if not pay_by_date:
            return
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.executemany('UPDATE shifts SET daily_pay = ? WHERE date = ?', pay_by_date)
        conn.commit()
        conn.close()
    
    def get_next_shift(self):
        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # 今日の日付以降で、予定が入っている最初の日を取得
        cursor.execute('''
            SELECT * FROM shifts 
            WHERE date >= ? AND start_plan != "" 
            ORDER BY date ASC LIMIT 1
        ''', (today,))
        row = cursor.fetchone()
        conn.close()
        return row

    def delete_shift(self, date):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM shifts WHERE date = ?', (date,))
        conn.commit()
        conn.close()

# --- 給与計算ロジック（最適化済み） ---
class PayCalculator:
    def __init__(self, db):
        self.db = db
        # 設定値を読み込み：１基本時給　２深夜割増　３休日追加時給
        self.hourly_wage = int(db.get_setting('hourly_wage'))
        self.night_rate = float(db.get_setting('night_rate'))
        self.holiday_add = int(db.get_setting('holiday_add'))

    # 深夜料金が発生する時間がどれくらい重なってるかの計算
    def _get_overlap_minutes(self, start_dt, end_dt, range_start, range_end):
        """指定された期間(start_dt ~ end_dt)と、特定の時間帯(range)の重複分数を返す"""
        latest_start = max(start_dt, range_start)
        earliest_end = min(end_dt, range_end)
        # 差分を秒で取得、その後分に変換し返す。
        delta = (earliest_end - latest_start).total_seconds()
        if delta > 0:
            return delta / 60
        return 0

    def calculate_daily_pay(self, shift_row):
        """
        給与計算ロジック
        ループ処理を廃止し、時間重複の計算により高速化・正確化
        """
        if not shift_row or not shift_row['start_actual'] or not shift_row['end_actual']:
            return 0

        fmt = '%H:%M'
        try:
            date_base = datetime.strptime(shift_row['date'], '%Y-%m-%d')
            start_dt = datetime.strptime(shift_row['start_actual'], fmt)
            end_dt = datetime.strptime(shift_row['end_actual'], fmt)
            
            # 日付合わせ
            start_dt = start_dt.replace(year=date_base.year, month=date_base.month, day=date_base.day)
            end_dt = end_dt.replace(year=date_base.year, month=date_base.month, day=date_base.day)

            # 日またぎ対応
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
        except ValueError:
            return 0

        # 総労働時間（分）
        total_minutes = (end_dt - start_dt).total_seconds() / 60
        if total_minutes <= 0:
            return 0

        # --- 深夜時間の算出 ---
        # 深夜帯定義: 
        # 1. 当日の00:00 - 05:00 (早朝深夜)
        # 2. 当日の22:00 - 翌日の05:00 (夜間深夜)
        
        night_minutes = 0
        
        # 1. 早朝深夜 (00:00 - 05:00)
        early_morning_start = date_base.replace(hour=0, minute=0)
        early_morning_end = date_base.replace(hour=5, minute=0)
        night_minutes += self._get_overlap_minutes(start_dt, end_dt, early_morning_start, early_morning_end)

        # 2. 夜間深夜 (22:00 - 翌05:00)
        night_start = date_base.replace(hour=22, minute=0)
        night_end = date_base.replace(hour=5, minute=0) + timedelta(days=1)
        night_minutes += self._get_overlap_minutes(start_dt, end_dt, night_start, night_end)

        # 通常時間
        normal_minutes = total_minutes - night_minutes

        # --- 給与計算 ---
        # 基本時給（休日なら加算）
        base_wage = self.hourly_wage
        if shift_row['is_holiday']:
            base_wage += self.holiday_add
            
        # 深夜時給
        night_wage = base_wage * self.night_rate

        # 総支給額 (分単位計算)
        gross_pay = (normal_minutes * (base_wage / 60)) + (night_minutes * (night_wage / 60))

        # 休憩控除 (単純に基本時給分を引く)
        break_min = shift_row['break_min'] if shift_row['break_min'] else 0
        deduction = break_min * (base_wage / 60)

        final_pay = max(0, gross_pay - deduction)
        
        return int(final_pay)

# --- 設定ダイアログ ---
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, db):
        super().__init__(parent)
        self.title("設定")
        self.geometry("300x250")
        self.db = db
        self.create_widgets()

    def create_widgets(self):
        pad = {'padx': 10, 'pady': 5}
        
        tk.Label(self, text="基本時給 (円):").pack(**pad)
        self.wage_var = tk.StringVar(value=self.db.get_setting('hourly_wage'))
        tk.Entry(self, textvariable=self.wage_var).pack(**pad)
        # pack(**pad)：事前に設定した引数を用いて渡す式


        tk.Label(self, text="深夜割増倍率 (例: 1.25):").pack(**pad)
        self.night_var = tk.StringVar(value=self.db.get_setting('night_rate'))
        tk.Entry(self, textvariable=self.night_var).pack(**pad)

        tk.Label(self, text="休日手当 (時給に＋〇〇円):").pack(**pad)
        self.holiday_var = tk.StringVar(value=self.db.get_setting('holiday_add'))
        tk.Entry(self, textvariable=self.holiday_var).pack(**pad)

        tk.Button(self, text="保存", command=self.save, bg="#4CAF50", fg="white").pack(pady=20)

    def save(self):
        try:
            int(self.wage_var.get())
            float(self.night_var.get())
            int(self.holiday_var.get())
            
            self.db.update_setting('hourly_wage', self.wage_var.get())
            self.db.update_setting('night_rate', self.night_var.get())
            self.db.update_setting('holiday_add', self.holiday_var.get())

            if hasattr(self.master, "recompute_all_daily_pay"):
                self.master.recompute_all_daily_pay()
                self.master.update_stats()
            
            messagebox.showinfo("成功", "設定を保存しました")
            self.destroy()
        except ValueError:
            messagebox.showerror("エラー", "数値で入力してください")

# --- シフト入力ダイアログ ---
class ShiftDialog(tk.Toplevel):
    def __init__(self, parent, db, date_str, on_close_callback):
        super().__init__(parent)
        self.title(f"{date_str} の詳細")
        self.db = db
        self.date_str = date_str
        self.callback = on_close_callback
        self.geometry("350x450")
        
        self.shift_data = self.db.get_shift(date_str)
        self.create_widgets()

    def create_widgets(self):
        sp = self.shift_data['start_plan'] if self.shift_data else ""
        ep = self.shift_data['end_plan'] if self.shift_data else ""
        sa = self.shift_data['start_actual'] if self.shift_data else ""
        ea = self.shift_data['end_actual'] if self.shift_data else ""
        br = self.shift_data['break_min'] if self.shift_data else 0
        ih = self.shift_data['is_holiday'] if self.shift_data else 0
        mm = self.shift_data['memo'] if self.shift_data else ""

        pad = {'padx': 10, 'pady': 5, 'sticky': 'w'}

        lf_plan = tk.LabelFrame(self, text="【予定】シフト", fg="blue")
        lf_plan.pack(fill="x", padx=10, pady=5)
        
        tk.Label(lf_plan, text="開始 (HH:MM)").grid(row=0, column=0, **pad)
        self.sp_var = tk.StringVar(value=sp)
        entry_sp = tk.Entry(lf_plan, textvariable=self.sp_var, width=10)
        entry_sp.grid(row=0, column=1, **pad)
        entry_sp.bind('<KeyRelease>', self.auto_colon)

        tk.Label(lf_plan, text="終了 (HH:MM)").grid(row=1, column=0, **pad)
        self.ep_var = tk.StringVar(value=ep)
        entry_ep = tk.Entry(lf_plan, textvariable=self.ep_var, width=10)
        entry_ep.grid(row=1, column=1, **pad)
        entry_ep.bind('<KeyRelease>', self.auto_colon)

        lf_act = tk.LabelFrame(self, text="【実績】勤務完了後に入力", fg="green")
        lf_act.pack(fill="x", padx=10, pady=5)

        tk.Label(lf_act, text="開始 (HH:MM)").grid(row=0, column=0, **pad)
        self.sa_var = tk.StringVar(value=sa)
        entry_sa = tk.Entry(lf_act, textvariable=self.sa_var, width=10)
        entry_sa.grid(row=0, column=1, **pad)
        entry_sa.bind('<KeyRelease>', self.auto_colon)

        tk.Label(lf_act, text="終了 (HH:MM)").grid(row=1, column=0, **pad)
        self.ea_var = tk.StringVar(value=ea)
        entry_ea = tk.Entry(lf_act, textvariable=self.ea_var, width=10)
        entry_ea.grid(row=1, column=1, **pad)
        entry_ea.bind('<KeyRelease>', self.auto_colon)

        tk.Label(lf_act, text="休憩 (分)").grid(row=2, column=0, **pad)
        self.br_var = tk.IntVar(value=br)
        tk.Entry(lf_act, textvariable=self.br_var, width=10).grid(row=2, column=1, **pad)

        lf_other = tk.LabelFrame(self, text="その他")
        lf_other.pack(fill="x", padx=10, pady=5)

        self.ih_var = tk.BooleanVar(value=bool(ih))
        tk.Checkbutton(lf_other, text="休日・祝日手当を適用する", variable=self.ih_var).pack(anchor='w', padx=10)

        tk.Label(lf_other, text="メモ").pack(anchor='w', padx=10)
        self.memo_var = tk.StringVar(value=mm)
        tk.Entry(lf_other, textvariable=self.memo_var).pack(fill='x', padx=10, pady=5)

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="保存", command=self.save, bg="#4CAF50", fg="white", width=10).pack(side='left', padx=5)
        tk.Button(btn_frame, text="削除", command=self.delete, bg="#f44336", fg="white", width=10).pack(side='left', padx=5)

    def auto_colon(self, event):
        if event.keysym == 'BackSpace': return
        entry = event.widget
        text = entry.get()
        if len(text) == 2 and text.isdigit():
            entry.insert(tk.END, ":")

    def validate_time(self, time_str):
        if not time_str: return True
        try:
            datetime.strptime(time_str, '%H:%M')
            return True
        except ValueError:
            return False

    def save(self):
        sp, ep = self.sp_var.get(), self.ep_var.get()
        sa, ea = self.sa_var.get(), self.ea_var.get()
        
        if not all(map(self.validate_time, [sp, ep, sa, ea])):
            messagebox.showerror("エラー", "時刻は HH:MM 形式で入力してください（例: 09:00, 22:30）")
            return

        pay_calc = PayCalculator(self.db)
        daily_pay = pay_calc.calculate_daily_pay({
            'date': self.date_str,
            'start_actual': sa,
            'end_actual': ea,
            'break_min': self.br_var.get(),
            'is_holiday': 1 if self.ih_var.get() else 0
        })

        self.db.upsert_shift(
            self.date_str, sp, ep, sa, ea,
            self.br_var.get(),
            1 if self.ih_var.get() else 0,
            self.memo_var.get(),
            daily_pay
        )
        self.callback()
        self.destroy()

    def delete(self):
        if messagebox.askyesno("確認", "この日のデータを削除しますか？"):
            self.db.delete_shift(self.date_str)
            self.callback()
            self.destroy()

# --- メインアプリケーション ---
class MainApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("バイト勤務管理システム")
        self.geometry("1000x700") # 初期サイズ
        self.minsize(800, 600)    # 最小サイズ制限
        
        # 【重要】リサイズ対応設定
        self.columnconfigure(1, weight=1) # 右側パネルを伸縮可能に
        self.rowconfigure(0, weight=1)    # 上下にも伸縮可能に

        calendar.setfirstweekday(calendar.SUNDAY)

        self.db = DatabaseManager(DB_NAME)
        if self.db.needs_pay_backfill:
            self.recompute_all_daily_pay()
        
        self.current_date = datetime.now()
        self.year = self.current_date.year
        self.month = self.current_date.month

        self.create_layout()
        self.refresh_all()

    def create_layout(self):
        # --- 左パネル（情報・設定） 固定幅 ---
        self.left_panel = tk.Frame(self, width=250, bg='#f0f0f0', padx=10, pady=10)
        self.left_panel.grid(row=0, column=0, sticky='ns') # packからgridに変更し、上下追従
        self.left_panel.pack_propagate(False)

        # --- 右パネル（カレンダー） 可変幅 ---
        self.right_panel = tk.Frame(self, bg='white', padx=10, pady=10)
        self.right_panel.grid(row=0, column=1, sticky='nsew') # 全方向に追従

        # 左パネルの中身
        tk.Label(self.left_panel, text="勤務情報", font=('Arial', 14, 'bold'), bg='#f0f0f0').pack(pady=(0, 20))
        
        self.lbl_next_shift_title = tk.Label(self.left_panel, text="【次回出勤】", bg='#f0f0f0', fg='#555')
        self.lbl_next_shift_title.pack(anchor='w')
        self.lbl_next_shift = tk.Label(self.left_panel, text="--/-- --:--", font=('Arial', 12), bg='#f0f0f0')
        self.lbl_next_shift.pack(anchor='w', pady=(0, 20))

        tk.Frame(self.left_panel, height=2, bd=1, relief='sunken').pack(fill='x', pady=10)

        self.create_stat_label("今月の稼ぎ", "monthly_pay")
        self.create_stat_label("今年の稼ぎ", "yearly_pay")
        self.create_stat_label("累計稼ぎ", "total_pay")

        tk.Frame(self.left_panel, height=2, bd=1, relief='sunken').pack(fill='x', pady=20)

        tk.Button(self.left_panel, text="⚙ 時給・割増設定", command=self.open_settings).pack(fill='x')

        # 右パネルの中身
        header_frame = tk.Frame(self.right_panel, bg='white')
        header_frame.pack(fill='x', pady=(0, 10))
        
        tk.Button(header_frame, text="<< 前月", command=self.prev_month).pack(side='left')
        self.lbl_cal_title = tk.Label(header_frame, text="", font=('Arial', 16, 'bold'), bg='white')
        self.lbl_cal_title.pack(side='left', expand=True)
        tk.Button(header_frame, text="次月 >>", command=self.next_month).pack(side='right')

        # カレンダー本体フレーム
        self.cal_frame = tk.Frame(self.right_panel, bg='white')
        self.cal_frame.pack(fill='both', expand=True) # 親に合わせて拡大

    def create_stat_label(self, title, var_name):
        tk.Label(self.left_panel, text=f"【{title}】", bg='#f0f0f0', fg='#555').pack(anchor='w')
        lbl = tk.Label(self.left_panel, text="¥0", font=('Arial', 14, 'bold'), fg='#2196F3', bg='#f0f0f0')
        lbl.pack(anchor='e', pady=(0, 10))
        setattr(self, f"lbl_{var_name}", lbl)
        #≒"lbl_var_name = lbl
        #なんでこんなめんどい書き方してるの？
        #pythonは変数名を文字列から動的に作るのが基本出来ない。
        #なので一度汎用変数名（lbl）でオブジェクトを作った後、setattr()で使いたい文字列にそのオブジェクトをぶち込むことで、
        #どんだけ関数を回しても、引数の文字列が違えば、別のオブジェクトとして扱うことができる。

    def refresh_all(self):
        self.draw_calendar()
        self.update_stats()
        self.update_next_shift()

    def draw_calendar(self):
        for widget in self.cal_frame.winfo_children():
            widget.destroy()

        self.lbl_cal_title.config(text=f"{self.year}年 {self.month}月")

        # 曜日ヘッダー
        for i, day in enumerate(WEEK_DAYS):
            fg_color = 'red' if i == 0 else 'blue' if i == 6 else 'black'
            # 曜日ラベルも伸縮するように sticky='nsew' を指定
            tk.Label(self.cal_frame, text=day, bg='#ddd', fg=fg_color).grid(row=0, column=i, sticky='nsew')
            self.cal_frame.grid_columnconfigure(i, weight=1) # 横方向の伸縮許可

        today_str = datetime.now().strftime('%Y-%m-%d')
        cal_data = calendar.monthcalendar(self.year, self.month)
        
        start_date = f"{self.year}-{self.month:02d}-01"
        _, last_day = calendar.monthrange(self.year, self.month)
        end_date = f"{self.year}-{self.month:02d}-{last_day}"
        shifts = {row['date']: row for row in self.db.get_shifts_by_range(start_date, end_date)}

        for r, week in enumerate(cal_data):
            self.cal_frame.grid_rowconfigure(r+1, weight=1) # 縦方向の伸縮許可
            for c, day in enumerate(week):
                if day == 0:
                    continue
                
                date_str = f"{self.year}-{self.month:02d}-{day:02d}"
                shift = shifts.get(date_str)
                
                bg_color = 'white'
                if c == 0: bg_color = '#FFEBEE'
                elif c == 6: bg_color = '#E3F2FD'
                
                # --- 表示テキストの改善（開始～終了を表示） ---
                info_text = f"{day}\n"
                if shift:
                    if shift['start_actual'] and shift['end_actual']:
                        bg_color = '#C8E6C9'
                        # 改行して終了時間も表示
                        info_text += f"実 {shift['start_actual']}\n～{shift['end_actual']}"
                    elif shift['start_plan']:
                        bg_color = '#BBDEFB'
                        end_p = shift['end_plan'] if shift['end_plan'] else ""
                        info_text += f"予 {shift['start_plan']}\n～{end_p}"

                fg_color = 'black'
                relief_style = 'flat'
                border_width = 1
                font_style = ('Arial', 9)

                if date_str == today_str:
                    fg_color = '#E65100'
                    relief_style = 'solid'
                    border_width = 2
                    font_style = ('Arial', 10, 'bold')
                    if not shift:
                        bg_color = '#FFF9C4' 

                btn = tk.Button(
                    self.cal_frame, text=info_text, bg=bg_color, fg=fg_color,
                    justify='left', anchor='nw',
                    relief=relief_style, bd=border_width, font=font_style,
                    command=lambda d=date_str: self.open_shift_dialog(d)
                )
                # ボタンを全方向に引き伸ばす
                btn.grid(row=r+1, column=c, sticky='nsew', padx=1, pady=1)

    def open_shift_dialog(self, date_str):
        ShiftDialog(self, self.db, date_str, self.refresh_all)

    def open_settings(self):
        SettingsDialog(self, self.db)

    def update_stats(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        now = datetime.now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        start_month = f"{now.year}-{now.month:02d}-01"
        end_month = f"{now.year}-{now.month:02d}-{last_day:02d}"
        start_year = f"{now.year}-01-01"
        end_year = f"{now.year}-12-31"

        cursor.execute("SELECT COALESCE(SUM(daily_pay), 0) FROM shifts WHERE date BETWEEN ? AND ?", (start_month, end_month))
        monthly = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COALESCE(SUM(daily_pay), 0) FROM shifts WHERE date BETWEEN ? AND ?", (start_year, end_year))
        yearly = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COALESCE(SUM(daily_pay), 0) FROM shifts")
        total = cursor.fetchone()[0] or 0
        conn.close()

        self.lbl_monthly_pay.config(text=f"¥{monthly:,}")
        self.lbl_yearly_pay.config(text=f"¥{yearly:,}")
        self.lbl_total_pay.config(text=f"¥{total:,}")

    def recompute_all_daily_pay(self):
        pay_calc = PayCalculator(self.db)
        rows = self.db.get_all_shifts()
        pay_by_date = []
        for row in rows:
            pay_by_date.append((pay_calc.calculate_daily_pay(row), row['date']))
        self.db.update_daily_pays(pay_by_date)

    def update_next_shift(self):
        row = self.db.get_next_shift()
        if row:
            d = datetime.strptime(row['date'], '%Y-%m-%d')
            wd_str = WEEK_DAYS[(d.weekday() + 1) % 7]
            self.lbl_next_shift.config(text=f"{d.month}/{d.day}({wd_str}) {row['start_plan']}~")
        else:
            self.lbl_next_shift.config(text="予定なし")

    def prev_month(self):
        if self.month == 1:
            self.month = 12
            self.year -= 1
        else:
            self.month -= 1
        self.refresh_all()

    def next_month(self):
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1
        self.refresh_all()

if __name__ == "__main__":
    app = MainApp()
    app.mainloop()
