from __future__ import annotations

import re


URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
TICKET_PATTERN = re.compile(r"TicketID[=:]?[A-Za-z0-9%._-]+", re.IGNORECASE)
INN_PATTERN = re.compile(r"\b(?:ИНН[:\s]*)?(\d{10}|\d{12})\b")
CONTRACT_PATTERNS = (
    re.compile(r"\b([A-Z]{4}\s?\d{6}-?\d)\b"),
    re.compile(r"\b([A-Z]{4}\s?\d{7})\b"),
    re.compile(r"\b([A-Z]{4}\d{8})\b"),
)


def compact_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_contract(value: str) -> str:
    return value.replace(" ", "").replace("--", "-")


def extract_entities(text: str) -> dict[str, list[str]]:
    contracts: set[str] = set()
    for pattern in CONTRACT_PATTERNS:
        for match in pattern.findall(text):
            contracts.add(normalize_contract(match))

    inns = sorted(set(INN_PATTERN.findall(text)))
    ticket_ids = sorted(set(TICKET_PATTERN.findall(text)))
    urls = sorted(set(URL_PATTERN.findall(text)))

    return {
        "contracts": sorted(contracts),
        "inns": inns,
        "ticket_ids": ticket_ids,
        "urls": urls,
    }


async def dtect_inn_cont(data: str) -> tuple[set[str], list[str]]:
    entities = extract_entities(data)
    return set(entities["contracts"]), entities["inns"]
