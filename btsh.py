import json
from typing import Dict, List
from dataclasses import dataclass
import httpx
import asyncio
from datetime import datetime, timedelta
from icecream import ic
import requests


@dataclass
class UrlSettings:
    headers2: Dict[str, str]
    cookies: Dict[str, str]
    headers: Dict[str, str]
        

headers2 = {
    'accept': '*/*',
    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
    # 'cookie': 'PHPSESSID=e64d7ed26d1bd1c6aca6aea85d704942; ChatBox=true',
    'dnt': '1',
    'origin': 'https://cab.seafishport.ru',
    'priority': 'u=1, i',
    'referer': 'https://cab.seafishport.ru/req.php',
    'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'sec-gpc': '1',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'x-requested-with': 'XMLHttpRequest',
}

data3 = {
    'ReqType': '5',
    'Vgm': '',
    'SubType': '-1',
    'ReqReport': '0',
    'Comment': '',
    'infoContact': '123',
    'Username': '',
    'expInn': '7802876660',
    'AddReq': '1',
    'Container': 'SEGU9865283',
    'Mode': 'undefined',
    'Express': '0',
    'LevelType': '',
    'inspectionType': '',
    'Docker': '',
    'Loader': '',
}


cookies = {
    'PHPSESSID': 'e64d7ed26d1bd1c6aca6aea85d704942',
    'ChatBox': 'true',
}


headers = {
    'accept': '*/*',
    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
    # 'cookie': 'PHPSESSID=e64d7ed26d1bd1c6aca6aea85d704942; ChatBox=true',
    'dnt': '1',
    'origin': 'https://cab.seafishport.ru',
    'priority': 'u=1, i',
    'referer': 'https://cab.seafishport.ru/release.php',
    'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'sec-gpc': '1',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'x-requested-with': 'XMLHttpRequest',
}




class UrlSender:
    
    def __init__(self):
        self.data = UrlSettings(headers2=headers2, cookies=cookies, headers=headers)
        self.session = httpx.AsyncClient()
        # self.session = requests.Session()
    
    async def connect(self, inn: str, conteiner_data: List[str]):
        url = 'https://cab.seafishport.ru/php_ajax/ajax_submit_reqadd.php'
        for conteiner in conteiner_data:
            data3 = {
				'ReqType': '5',
				'Vgm': '',
				'SubType': '-1',
				'ReqReport': '0',
				'Comment': '',
				'infoContact': '123',
				'Username': '',
				'expInn': '7802876660',
				'AddReq': '1',
				'Container': conteiner.replace(' ', ''),
				'Mode': 'undefined',
				'Express': '0',
				'LevelType': '',
				'inspectionType': '',
				'Docker': '',
				'Loader': '',
			}
            response = await self.session.post(url, cookies=self.data.cookies, headers=self.data.headers2, data=data3)
            print(response.text)
        
        
    async def update_cookie(self, cookie: dict):
        self.data.cookies = cookie
            
            
    async def connect_relise(self, inn: str, cont_numbers: List[str]):
        url = 'https://cab.seafishport.ru/php_ajax/ajax_submit_releaseadd.php'
        current_date = datetime.now()
        formatted_current_date = current_date.strftime("%d.%m.%Y")
        print("Текущая дата:", formatted_current_date)

        
        one_week_later = current_date + timedelta(weeks=1)
        formatted_week_later_date = one_week_later.strftime("%d.%m.%Y")
        ic(inn)
        for conteiner in cont_numbers:
            ic(conteiner.replace(" ", ""))
            data3 = (
                f'action=submit_releaseadd&Line=Baltic Shipping Agency&fio=&ReleaseNumber={formatted_current_date}{conteiner.replace(" ", "")}'
                f'&ReleaseDate={formatted_current_date}&Container={conteiner.replace(" ", "")}&Expediter={inn}&ReleaseExpiry={formatted_week_later_date}'
            )
            print(data3)
            response = await self.session.post(url, cookies=self.data.cookies, headers=headers, data=data3)
            
            result = response.text
        return result




urlsender = UrlSender()


async def main():
    connect = UrlSender()
    await connect.connect_relise('7805749758', ['SEGU9869139'])
    

if __name__ == "__main__":
    asyncio.run(main())
    