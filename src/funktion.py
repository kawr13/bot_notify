import requests
from 
from io import BytesIO
from PIL import Image
import pytesseract

# Создаем сессию
session = requests.Session()

# Получаем изображение капчи и токен капчи
response = session.get('https://cab.seafishport.ru/auth.php')
captcha_data = response.content
captcha_token = response.headers[]

# Решаем капчу с помощью библиотеки pytesseract
captcha_text = pytesseract.image_to_string(Image.open(BytesIO(captcha_data)))

# Подготавливаем данные для отправки POST-запроса на авторизацию, включая логин, пароль и решение капчи
data = {
    'login': 'Iwans',
    'password': 'jh02tbdz',
    'captcha': captcha_text,
    'captcha_token': captcha_token
}

# Отправляем POST-запрос на авторизацию, передав cookie и данные
response = session.post('https://cab.seafishport.ru/auth.php', data=data)

# Проверяем, удалось ли авторизоваться
if 'Выход' in response.text:
    print('Авторизация прошла успешно')
else:
    print('Ошибка авторизации')
