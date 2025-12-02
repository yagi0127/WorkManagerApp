import tkinter as tk
from tkinter import ttk, messagebox
import calendar
import sqlite3
from datetime import datetime, timedelta

DB_NAME = "work_schedule.db"

class DatabaseManager:
    def __init__(self,db_name):
        #すべてのdefで使えるようself化
        self.db_name = db_name
        self.init_db()

    #最初にデータベースを実体化させるところ
    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                    date TEXT PRIMARY KEY,
                    start_plan TEXT,
                    end_plan TEXT,
                    start_actual TEXT,
                    end_actual TEXT,
                    break_min INTEGER DEFAULT 0,
                    is_holiday INTEGER DEFAULT 0,
                    memo TEXT
                    )
        ''')

        #キーバリュー形式
        cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
            )
        
        ''')
                
        # デフォルト設定（これがないと計算できないからね！）
        default_settings = {
            'hourly_wage': '1000',
            'night_rate': '1.25',
            'holiday_add': '0'
        }
        
        for key, value in default_settings.items():
            # INSERT OR IGNORE: データがなければ入れる、あれば何もしない
            cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        # ----------------------

        conn.commit()
        conn.close()

class Mainapp (tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("バイト勤怠管理アプリ")
        self.geometry("1000x700")
        calendar.setfirstweekday(calendar.SUNDAY)
        self.current_data = datetime.now()
        self.year = self.current_data.year
        self.month = self.current_data.month

        self.create_layout()
        self.draw_calendar()


    #カレンダーを作る
    def draw_calendar(self):
        #表示されてるものを一つずつ読んで消していく。（子を全部殺す）
        for widget in self.cal_frame.winfo_children():
            widget.destroy()

        #ヘッダーのタイトル（年月）を更新する。
        #create_layoutのlbl_cal_titileがある前提なのでそっちを作っておく
        self.lbl_cal_title.config(text=f"{self.year}年 {self.month}日")

        #曜日表示
        week_days = ["日", "月", "火", "水", "木", "金", "土"]

        #繰り返しで日付表示＋色分け enumerateは要素とともにインデックスも得ることができる
        for i, day_name in enumerate(week_days):
            #インデックスの値をもとに色決め
            fg_color = "black"
            if i == 0: fg_color = 'red'
            if i == 6: fg_color = 'blue'

            #gridで表形式の表示。stickyは枠いっぱいに広がってという意味
            lbl = tk.Label(self.cal_frame, text=day_name, bg="#ddd", fg=fg_color)
            lbl.grid(row=0, column=i, sticky="nsew")
        
        #こっちは親が用意する領域を横の方向に均等に枠いっぱいに広げてという意味
        for i in range(7):
            self.cal_frame.grid_columnconfigure(i, weight=1)

        
        #ここから日付ボタンを作っていく
        #その月の週ごとのカレンダーを取得できる
        cal_data = calendar.monthcalendar(self.year, self.month)

        #比較用の今日の日付をここで準備。strftimeで取得する時間データを指定通りに整える。
        today_str = datetime.now().strftime("%Y-%m-%d")

        #二重ループでボタンを設置していく
        for r, week in enumerate(cal_data):
            #曜日と日付を取り出す
            for c, day in enumerate(week):
                #日付がはいいてないところは０なのでそれをスキップ
                if day == 0:
                    continue

                #現在の日付を比較用に整形する
                current_date_str = f"{self.year}-{self.month:02d}-{day:02d}"

                #日付ボタンの基本デザインを指定
                bg_color = "white"
                fg_color = "black"
                font_style = ("Arial", 9)
                relief_style = "flat" #枠線なし
                border_width = 1

                #土日の場合背景色を変える
                if c == 0: bg_color = "#FFEBEE"
                if c == 6: bg_color = "#E3F2FD"

                #もし今日と現在の日付が一致したらデザインを変更
                if current_date_str == today_str:
                    bg_color = "#FFF9C4"
                    fg_color = "red"
                    font_style = ("Arial", 10, "bold")
                    relief_style = "solid"
                    border_width = 1.5


                #ボタンを作っていく。テキストに日付を入れて左上から詰めていく
                btn = tk.Button(self.cal_frame, text=str(day),bg=bg_color,fg=fg_color, font=font_style, relief=relief_style, bd=border_width, anchor="nw")
                #設置するときは表形式のgrid。ただ一行目は曜日に使ってるので２行目から設置する。
                btn.grid(row=r+1, column=c, sticky="nsew")


        #横方向は曜日のラベルでやってるので、次は縦方向に週の数だけ均等に枠いっぱい広げる
        for i in range(1, len(cal_data) + 1): #1は曜日のラベルの分
            self.cal_frame.grid_rowconfigure(i, weight=1)



    def create_layout(self):
        #画面を左右に分ける
        self.left_panel = tk.Frame(self, width=250, bg="#c1c1c1", padx=10,pady=10)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)#サイズ固定

        self.right_panel = tk.Frame(self,bg="#dddddd", padx=10, pady=10)
        self.right_panel.pack(side='right', fill="both", expand=True)#残りの領土をもらう＋その範囲一杯にカラー

        #左側のフレーム上部に月移動ボタンと年月日表示追加
        header_frame = tk.Frame(self.right_panel, bg="#ffffff")
        header_frame.pack(fill="x",pady=(0,10))
        tk.Button(header_frame,text="前月", command=self.prev_month).pack(side="left")
        self.lbl_cal_title = tk.Label(header_frame, text="", font=("Arial", 16, "bold"), bg="#ffffff")
        self.lbl_cal_title.pack(side="left", expand=True)
        tk.Button(header_frame, text="次月", command=self.next_month).pack(side="right")

        #カレンダーを置くフレームの設定
        self.cal_frame = tk.Frame(self.right_panel, bg="#ffffff")
        self.cal_frame.pack(fill="both", expand=True)



    def prev_month(self):
        pass
    
    def next_month(self):
        pass

if __name__ == "__main__":
    # db = DatabaseManager(DB_NAME)
    # print("データベースとテーブルが完成したよ")
    app = Mainapp()
    app.mainloop()