from tkinter import *
import time

def toggle_fullscreen(event):
    # Функция для развертывания/сворачивания окна
    state = tk.attributes("-fullscreen")
    tk.attributes("-fullscreen", not state)

def resize_window(event):
    # Функция для изменения размеров окна
    width = event.width
    height = event.height
    canvas.config(width=width, height=height)

tk = Tk()
tk.title('Разбираем ООП')
tk.resizable(0, 0)
tk.wm_attributes('-topmost', 1)
canvas = Canvas(tk, width=800, height=600, highlightthickness=0)
canvas.pack()

# Привязываем функцию toggle_fullscreen к нажатию клавиши F11
tk.bind("<F11>", toggle_fullscreen)
# Привязываем функцию resize_window к изменению размеров окна
tk.bind("<Configure>", resize_window)

tk.update()

while True:
    tk.update_idletasks()
    tk.update()
    time.sleep(0.01)
