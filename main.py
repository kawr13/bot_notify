import traceback
from os.path import split
from turtle import st
from typing import List
import psutil
import subprocess
import time
from aiogram import types, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, Message, ContentType
from aiogram.fsm.context import FSMContext
from aioimaplib import IMAP4_SSL
from dotenv import load_dotenv
from dotenv import load_dotenv
import email
import json
import os
import winsound
import asyncio
import quopri
from random import randint
from app import dp, bot
#import script2
from urllib.parse import quote
import re
import aiofiles
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from test import get_global, set_global
import test
from utilites import dtect_inn_cont
from aiogram.fsm.state import State, StatesGroup
from btsh import urlsender
from icecreamconfig import logger, ic


load_dotenv()
logger.track_library('aiogram')
logger.track_library('aioimaplib')

ANYDESK_PATH = 'C:\Program Files (x86)\AnyDesk\AnyDesk.exe'
PROCESS_NAME = 'AnyDesk.exe'
USERS_LIST_PATH = "users.json"
CONFIG_PATH = "config.json"
user_list: List[int] = [453994951, 366182656, 1023752757, 802783556]


async def add_user(data: List[int]):
    global user_list
    user_list.extend(data)
    user_list = list(set(user_list))


class CookieState(StatesGroup):
    waiting_for_cookie = State()
    end_for_cookie = State()


@dp.message(F.text == '/coockie') 
async def start(messages: Message, state: FSMContext):
    print(messages.text)
    await state.set_state(CookieState.waiting_for_cookie)
    web_app_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="отмена", callback_data="can_cel")
            ]
        ],
        resize_keyboard=True
    )
    
    await messages.answer('Скинте ваши куки', reply_markup=web_app_kb)
    await state.set_state(CookieState.end_for_cookie)

@dp.message(CommandStart())
async def starting(message: Message, state: FSMContext):
    await message.answer(f'Добро пожаловать в Боте уведомляторе {message.from_user.id}')
def kill_anydesk_processes(name: str, max_attempts: int = 3, delay: float = 1.0) -> bool:
    """
    Убивает все процессы, связанные с AnyDesk, включая экземпляры с суффиксами.
    
    Args:
        max_attempts: Максимальное количество попыток проверки процессов.
        delay: Задержка между попытками (в секундах).
    
    Returns:
        bool: True, если хотя бы один процесс был убит, иначе False.
    """
    found = False
    # Регулярное выражение для поиска AnyDesk (игнорирует суффиксы, регистр и .exe)
    anydesk_pattern = re.compile(r'anydesk.*', re.IGNORECASE)

    for attempt in range(max_attempts):
        logger.info(f"Попытка {attempt + 1} из {max_attempts}: поиск процессов AnyDesk...")
        processes_killed = 0
        
        # Проходим по всем процессам
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                proc_name = proc.info['name'].lower()
                # Проверяем, соответствует ли имя процесса шаблону AnyDesk
                if anydesk_pattern.match(proc_name):
                    proc.kill()  # Завершаем процесс
                    processes_killed += 1
                    found = True
                    logger.info(f"Процесс {proc_name} (PID: {proc.pid}) убит.")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                logger.warning(f"Не удалось убить процесс {proc_name} (PID: {proc.pid}): {e}")
                continue
        
        if processes_killed > 0:
            logger.info(f"Убито процессов AnyDesk: {processes_killed}")
        else:
            logger.info("Процессы AnyDesk не найдены на этой попытке.")
        
        # Если процессы были убиты, ждём, чтобы проверить, не появились ли новые
        if found and attempt < max_attempts - 1:
            time.sleep(delay)
    
    return found


async def start_process(executable_path: str) -> bool:
    """Запускает процесс по заданному пути."""
    if not os.path.exists(executable_path):
        print(f"Файл {executable_path} не найден!")
        return False
    try:
        subprocess.Popen(executable_path)  # Запускает AnyDesk
        print(f"Процесс {executable_path} запущен.")
        return True
    except Exception as e:
        print(f"Ошибка при запуске: {e}")
        return False
    
    
@dp.message(F.text == '/restart') 
async def restart_programm(messages: Message, state: FSMContext):

    if kill_anydesk_processes(PROCESS_NAME):
        print("Жду немного, чтобы процесс точно завершился...")
        time.sleep(2)  # Даём время на завершение
    else:
        print("AnyDesk не был найден среди запущенных процессов.")

    print("Запускаю AnyDesk заново...")
    await start_process(ANYDESK_PATH)
    await messages.answer('Анидеск перезапущен!')


@dp.callback_query(F.data == 'can_cel')
async def can_cel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer('Отменено')
    
    
@dp.message(CookieState.end_for_cookie)
async def update_cookie(message: Message, state: FSMContext):
    ic(message.text.replace("'", '"'))
    await urlsender.update_cookie(json.loads(message.text.replace("'", '"')))
    await state.clear()
    await message.answer('Куки обновлены')


def extract_url_from_email(email_text):
    # Декодируем весь текст из формата quoted-printable
    try:
        decoded_text = quopri.decodestring(email_text.encode('utf-8')).decode('utf-8', errors='ignore')

    except Exception as e:
        # Если возникают ошибки декодирования, можно логировать или пробовать другую кодировку
        raise ValueError(f"Ошибка декодирования текста: {e}")

    # Для отладки можно вывести результат декодирования
    # print(decoded_text)

    # Регулярное выражение для поиска URL, начинающегося с "[1]http://"
    # Если URL может содержать переносы, они уже объединены после декодирования.

    pattern = re.compile(r'\[1\](http://[^\s]+)')

    urls = pattern.findall(decoded_text)
    if len(urls) == 0:
        return None, None
    email_pattern_sub = re.compile(r'\[?\d*\]?(https?://[^\s]+?TicketID[^\s]+)')
    try:
        url =  quopri.decodestring(str(urls[0]).encode('utf-8')).decode('utf-8', errors='ignore')
    except Exception as e:
        logger.exception(e)
    decoded_text = email_pattern_sub.sub('', decoded_text)
    
    data1 = url.split(';')
    if len(data1) > 1:
        url_base, tiketid = data1
    else:
        tiketid = data1[0]
        url_base = 'base@basisport.ru'
    encoded_url = quote(tiketid, safe=':/?&=')

    ic(decoded_text, encoded_url.replace('%', '='),  url_base)
    result = url_base + ';' + encoded_url.replace('%', '=')
    return result.replace(' ', ''), decoded_text


async def play_notification_sound(msg_dek):
    """Play notification sound when new email arrives."""
    notification_sound_path = "sound.wav"
    if os.path.exists(notification_sound_path):
        # воспроизвести звуковой файл
        # winsound.PlaySound(notification_sound_path, winsound.SND_FILENAME)
        # text = msg_dek.split('\n')
        cont_list, inn = await dtect_inn_cont(msg_dek)
        cont_list = list(cont_list)
        keyboards = False
        keyboard_cont = None
        if cont_list and inn:
            keyboards = True
            
            global_dict = await get_global()
            global_dict[inn[0] + '_' + cont_list[0]] = cont_list
            await set_global(global_dict)
            keyboard_cont = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="отправить заявку", callback_data=ic(f"send_{cont_list[0]}")) # type: ignore
                    ]
                ],
                resize_keyboard=True
            )
        
        async def sender(item: str):
            await bot.send_message(item, 'Новое письмо', parse_mode='HTML')
            email_pattern = re.compile(r'<')

            urls, msg_decode = extract_url_from_email(msg_dek)
            if urls is None:
                pass
            if msg_decode is None:
                msg_decode = msg_dek
            ic(urls)
            pattern = r"^(.*?(http[s]?://[^\s]+?TicketID[^\s]*).*)$"
            email_pattern_sub = re.compile(pattern)
            msg_decode = email_pattern_sub.sub('', msg_decode)
            msg_decode = email_pattern.sub('', msg_decode)
            if urls:
                msg_decode += '\n\n' + urls
            if keyboards:

                await bot.send_message(item, msg_decode, parse_mode='HTML', reply_markup=keyboard_cont)
            else:
                await bot.send_message(item, msg_decode, parse_mode='HTML')
                
                
        tasks = [asyncio.create_task(sender(item)) for item in user_list]
        
        asyncio.gather(*tasks)
        # for item in user_list:
        #     await bot.send_message(item, 'Новое письмо', parse_mode='HTML')
        #     email_pattern = re.compile(r'<')

        #     urls, msg_decode = extract_url_from_email(msg_dek)
        #     if urls is None:
        #         pass
        #     if msg_decode is None:
        #         msg_decode = msg_dek
        #     ic(urls)
        #     pattern = r"^(.*?(http[s]?://[^\s]+?TicketID[^\s]*).*)$"
        #     email_pattern_sub = re.compile(pattern)
        #     msg_decode = email_pattern_sub.sub('', msg_decode)
        #     msg_decode = email_pattern.sub('', msg_decode)
        #     if urls:
        #         msg_decode += '\n\n' + urls
        #     if keyboards:

        #         await bot.send_message(item, msg_decode, parse_mode='HTML', reply_markup=keyboard_cont)
        #     else:
        #         await bot.send_message(item, msg_decode, parse_mode='HTML')


async def filter_email_subject(subject, ignore_keywords):
    """Filter email based on subject keywords to be ignored."""
    for keyword in ignore_keywords:
        if keyword.lower() in subject.lower():
            logger.info("Задача была пропущена")
            await asyncio.sleep(0.4)
            return True
    return False



async def decode_body(payload):
    if payload["Content-Transfer-Encoding"] == "quoted-printable":
        return quopri.decodestring(payload.get_payload(decode=True)).decode("utf-8")
    else:
        return payload.get_payload(decode=True).decode("utf-8")



async def check_new_emails(imap, email_address, password, ignore_keywords):
    try:
        await imap.wait_hello_from_server()
        await imap.login(email_address, password)
        await imap.select("inbox")
        status, messages = await imap.search(None, "UNSEEN")

        if status == "OK":
            # Если messages[0] – байты, декодируем их
            message_numbers = messages[0].decode().split() if isinstance(messages[0], bytes) else messages[0].split()

            for msg_num in message_numbers:
                try:
                    status, msg_data = await imap.fetch(msg_num, "(RFC822)")
                    ic(status, msg_data)

                    # Перебираем элементы msg_data и выбираем тот, который вероятнее всего является содержимым письма
                    raw_email = None
                    for item in msg_data:
                        if isinstance(item, (bytes, bytearray)):
                            # Если элемент достаточно длинный и содержит признак заголовка письма,
                            # например "Return-Path:", то считаем его содержимым письма.
                            if len(item) > 100 and b"Return-Path:" in item:
                                raw_email = bytes(item) if isinstance(item, bytearray) else item
                                break

                    if raw_email:
                        email_msg = email.message_from_bytes(raw_email)
                        msg_dek = None

                        if email_msg.is_multipart():
                            for part in email_msg.get_payload():
                                if part.get_content_type() == "text/plain":
                                    ic(part.get_payload())
                                    msg_dek = await decode_body(part)
                                    break
                        else:
                            msg_dek = await decode_body(email_msg)

                        if msg_dek is not None and not await filter_email_subject(msg_dek, ignore_keywords):
                            await play_notification_sound(msg_dek)
                    else:
                        logger.exception(f"Could not extract email content from message {msg_num}: {repr(msg_data)}")

                except Exception as e:
                    logger.exception(f"Error processing message {msg_num}: {e}")
                    logger.exception(f"Traceback: {traceback.format_exc()}")
                    continue

        await imap.close()
    except Exception as e:
        logger.exception(f"Error: {e}")
        logger.exception(f"Traceback: {traceback.format_exc()}")
    finally:
        await imap.logout()



async def main2():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    email_address: str | None = config["email_address"]
    password: str | None = os.getenv("password")

    imap_server = os.getenv("server")

    ignore_keywords = config.get("ignore_keywords", [])
    ic(email_address)
    while True:
        try:
            imap = IMAP4_SSL(host=imap_server)
            thre = await check_new_emails(imap, email_address, password, ignore_keywords)
            num = randint(30, 80)
            logger.info(f'До следующей проверки {num}')
            await asyncio.sleep(num)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            

async def main():
    if not os.path.exists(USERS_LIST_PATH):
        async with aiofiles.open(USERS_LIST_PATH, mode="w+", encoding="utf-8") as f:
            await f.write(json.dumps({
                "users_list": [453994951, 366182656, 1023752757, 802783556,],
            }))
    else:
        async with aiofiles.open(USERS_LIST_PATH, mode="r", encoding="utf-8") as f:
            data = await f.read()
            data = json.loads(data)
            ic(data)
            await add_user(data["users_list"])
    dp.include_routers(test.router,)
    await bot.send_message(453994951, 'Бот запущен', reply_markup=types.ReplyKeyboardRemove())
    await bot.send_message(802783556, 'Бот запущен', reply_markup=types.ReplyKeyboardRemove())
    try:
        await bot.send_message(1133887397, 'Бот запущен', reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
        print('error')
    task = asyncio.create_task(main2())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
    
    
