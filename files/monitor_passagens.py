#!/usr/bin/env python3
"""
Monitor de passagens aéreas com alerta no Telegram.
Roda uma checagem por execução (ideal para GitHub Actions ou cron).
Rotas e datas ficam no config.json; os tokens vêm de variáveis de ambiente.
"""
import itertools
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from apify_client import ApifyClient

CONFIG = json.loads(Path("config.json").read_text(encoding="utf-8"))
APIFY_TOKEN = os.environ["APIFY_TOKEN"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
ARQUIVO_ESTADO = Path("estado_precos.json")


def datas_entre(inicio, fim):
    d, f = date.fromisoformat(inicio), date.fromisoformat(fim)
    while d <= f:
        yield d.isoformat()
        d += timedelta(days=1)


def combinacoes(rota, limite):
    pares = [
        (i, v)
        for i, v in itertools.product(
            datas_entre(rota["ida_de"], rota["ida_ate"]),
            datas_entre(rota["volta_de"], rota["volta_ate"]),
        )
        if v > i
    ]
    pares.sort()  # datas mais próximas primeiro
    return pares[:limite]


def buscar_menor_preco(client, rota, ida, volta):
    run = client.actor("kuezi/flight-offers-api").call(
        logger=None,
        run_input={
            "endpoint": "flight-offers",
            "originLocationCode": rota["origem"],
            "destinationLocationCode": rota["destino"],
            "departureDate": ida,
            "returnDate": volta,
            "adults": CONFIG.get("adultos", 1),
            "currencyCode": CONFIG.get("moeda", "BRL"),
            "max": 5,
        },
    )
    resp = client.key_value_store(run["defaultKeyValueStoreId"]).get_record("RESPONSE")
    ofertas = (resp or {}).get("value", {}).get("data", [])
    precos = [float(o["price"]["grandTotal"]) for o in ofertas if o.get("price")]
    return min(precos) if precos else None


def enviar_telegram(texto):
    print(texto)
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    dados = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": texto}).encode()
    try:
        urllib.request.urlopen(url, dados, timeout=15)
    except Exception as e:
        print(f"[aviso] falha ao enviar ao Telegram: {e}")


def checar_rota(client, rota, estado):
    chave = f"{rota['origem']}-{rota['destino']}"
    moeda = CONFIG.get("moeda", "BRL")
    melhor = None
    for ida, volta in combinacoes(rota, CONFIG.get("max_combinacoes_por_rota", 6)):
        try:
            preco = buscar_menor_preco(client, rota, ida, volta)
        except Exception as e:
            print(f"[erro] {chave} {ida}->{volta}: {e}")
            continue
        print(f"{chave} {ida} / {volta}: {preco}")
        if preco is not None and (melhor is None or preco < melhor[0]):
            melhor = (preco, ida, volta)

    if melhor is None:
        return
    preco, ida, volta = melhor
    anterior = estado.get(chave, {}).get("menor_preco")
    alvo = rota.get("preco_alvo")
    msg = (f"✈️ {rota['origem']} → {rota['destino']}\n"
           f"Ida {ida} · Volta {volta}\nPreço: {moeda} {preco:,.2f}")

    if anterior is None:
        enviar_telegram(f"Monitoramento iniciado.\n{msg}")
    elif preco < anterior * 0.99:
        enviar_telegram(f"🔔 NOVA MENOR TARIFA (antes: {moeda} {anterior:,.2f})\n{msg}")
    elif alvo and preco <= alvo:
        enviar_telegram(f"🎯 Abaixo do seu preço-alvo ({moeda} {alvo})\n{msg}")

    if anterior is None or preco < anterior:
        estado[chave] = {"menor_preco": preco, "ida": ida, "volta": volta}


def main():
    client = ApifyClient(APIFY_TOKEN)
    estado = json.loads(ARQUIVO_ESTADO.read_text()) if ARQUIVO_ESTADO.exists() else {}
    for rota in CONFIG["rotas"]:
        checar_rota(client, rota, estado)
    ARQUIVO_ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
