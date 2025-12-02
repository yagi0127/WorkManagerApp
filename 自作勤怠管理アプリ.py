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
        self.geometry("800x600")
        self.create_layout()
    def create_calendar(self):
        pass
    def create_layout(self):
        #画面を左右に分ける
        self.left_panel = tk.Frame(self, width=250, bg="#393939", padx=10,pady=10)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)#サイズ固定

        self.right_panel = tk.Frame(self,bg="#717171", padx=10, pady=10)
        self.right_panel.pack(side='right', fill="both", expand=True)#残りの領土をもらう＋その範囲一杯にカラー


if __name__ == "__main__":
    # db = DatabaseManager(DB_NAME)
    # print("データベースとテーブルが完成したよ")
    app = Mainapp()
    app.mainloop()