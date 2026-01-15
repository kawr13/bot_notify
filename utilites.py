import re


async def dtect_inn_cont(data: str):
    #data = data.replace('-', '').replace('_','')
    math = re.findall(r'\b[A-Z]{4}\d{7}\d\b', data)		    
    math0 = re.findall(r'\b[A-Z]{4}\s?\d{6}-?\d|\b[A-Z]{4}\s?\d{7}\b', data)
    math1 = re.findall(r's*[A-Za-z]{4}\d{7}\b', data)
    math2 = re.findall(r's*[A-Za-z]{4}\s+\d{7}\b', data)
 
    inn_math = re.findall(r'\b(?:ИНН)?(\d{10}|\d{12})\b', data)
    
    print(math, math0, math1, math2)
    text = []
    if math or len(math) > 0:
        text += math
    if math0 or len(math0) > 0:
        print('сработал math0')
        text += math0
    if math1 or len(math1) > 0:
        text += math1
    if math2 or len(math2) > 0:
        text += math2
    print(text)
    # text = text.replace(' ', '').replace('-', '')
    return set(text), inn_math