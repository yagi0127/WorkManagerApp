import tkinter as tk
import calendar
import sqlite3
from datetime import datetime, timedelta


class DatabaseManager:
    def __init__(self,db_name):
        self.db_name = db_name
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                    date TEXT PRIMARY KEY,
                    start_plan TEXT,
                    end_plan TEXT,
                    start_actual TEXT,
                    
                    )
        ''')

        conn.commit()
        conn.close()
        pass

class Mainapp (tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("バイト勤怠管理アプリ")
        self.geometry("800x600")
    def create_calendar(self):
        pass
    def create_layout(self):
        pass

if __name__ == "__main__":
    app = Mainapp()
    app.mainloop()