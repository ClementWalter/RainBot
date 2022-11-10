# Script to update the tennis list
# First copy/paste the tennis var from the inner <script> tag of
# https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=tennisParisien&view=les_tennis_parisiens
# by searching var tennis = ...
# in a tennis.json file

#%% Imports
import json
from itertools import chain

import pandas as pd
import requests
from bs4 import BeautifulSoup

#%% Parse tennis list
response = requests.get(
    "https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=tennisParisien&view=les_tennis_parisiens"
)
soup = BeautifulSoup(response.text, features="html5lib")
script = soup.find("div", {"class": "map-container"}).text.replace("\n", "").replace("\t", "")
start = script.find("var tennis = ")
stop = script.find("var markers =")
tennis = [
    t["properties"]
    for t in json.loads(script[start:stop].replace("var tennis = ", "").replace(";", ""))[
        "features"
    ]
]

#%% Copy tennis to clipboard and paste to spreadsheet
(
    pd.DataFrame([t["general"] for t in tennis])
    .rename(columns=lambda c: c[1:])
    .assign(equCom=lambda df: df.equCom.str.replace(r"\n|\r", "", regex=True))
    .drop_duplicates(subset=["nomSrtm"])
    .to_clipboard(sep=";", index=False)
)

#%% Copy courts list to clipboard and paste as well
(
    pd.DataFrame(
        list(
            chain.from_iterable(
                [
                    [
                        {**c, "id": t["general"]["_id"], "surface": c["_coating"]["_revLib"]}
                        for c in t["courts"]
                    ]
                    for t in tennis
                ]
            )
        )
    )
    .rename(columns={"_airCvt": "couvert", "_airEcl": "eclaire", "_airOuvRes": "ouvert"})
    .filter(items=["_airId", "_airNom", "id", "surface", "eclaire", "ouvert", "couvert"])
    .drop_duplicates(subset=["_airId"])
    .to_clipboard(sep=";", index=False)
)
