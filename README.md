# Hydro One Green Button – automatikus Home Assistant-import

Saját Home Assistant-integráció a Hydro One óránkénti fogyasztási előzményeihez. A myAccount-portál által használt belépést és Green Button XML-exportot hívja meg, majd az órákat a Home Assistant hosszú távú energiastatisztikáiba írja.

**Állapot: 0.1.0, élő bejelentkezéssel még nem ellenőrzött első változat.** A feldolgozást, az átfedések kezelését és a letöltési kérések felépítését 28, személyes adatokat nem tartalmazó teszt ellenőrzi. A Recorder-hívásokat a célrendszer Home Assistant Core 2026.9.2 forrásával vetettük össze; a teljes integráció HA alatti futását még ellenőrizni kell.

## Működés

- Első alkalommal alapértelmezés szerint az előző 90 napot kéri le; 1–730 nap választható.
- Alapértelmezés szerint 24 óránként frissít. Mindig a tegnapig lezárt Toronto-naptári napokat kéri.
- Újra letölti az előző 14 napot, így a késve megjelenő és javított órák is bekerülnek. Hosszabb kiesés után a hiányzó időszakot is lekéri, a szolgáltató legfeljebb két éves ablakán belül.
- Azonos időbélyegnél felülírja a korábbi értéket, és újraszámolja a kumulatív statisztikát. Az ismételt letöltés nem adja hozzá még egyszer a fogyasztást.
- A forrás UTC-időbélyegeit megőrzi. A Wh-értékeket az XML `powerOfTenMultiplier` mezőjével váltja kWh-ra.
- A `ReadingType` és `MeterReading` hivatkozásai alapján csak az előremenő, 60 perces villamosenergia-fogyasztást fogadja el.
- Hiba esetén megőrzi a már importált órákat. Jelszóhiba esetén a Home Assistant új belépést kér.

A Hydro One által közzétett történeti adatokat importálja. Az adat megjelenése a szolgáltatótól függ; a frissítés nem teszi azokat valós idejű mérési adatokká.

## Telepítés

1. A ZIP `custom_components/hydroone_greenbutton` mappáját másold a Home Assistant konfigurációs könyvtárának `custom_components` mappájába. A végeredmény például `/config/custom_components/hydroone_greenbutton/manifest.json`.
2. Indítsd újra a Home Assistant Core-t.
3. **Settings → Devices & services → Add integration → Hydro One Green Button.**
4. Az emailt és jelszót közvetlenül a saját Home Assistant felületén add meg. Válaszd ki a mérőt és a kezdeti napok számát.
5. Sikeres import után az **Energy → Electricity grid → Add consumption** listájában válaszd a **Hydro One electricity (…)** statisztikát. A megjelenéshez szükség lehet az oldal frissítésére, illetve a Recorder importjának befejezésére.

Az import külső statisztika, azonosítója `hydroone_greenbutton:electricity_…`. A **Latest hour consumption** szenzor az utolsó órát mutatja; az Energia felületen a külső statisztikát válaszd. Az **Imported hours** szenzor `energy_statistic_id` attribútuma megadja a pontos azonosítót.

Az integráció beállításainál a frissítés 6–168 órára, az átfedő újraletöltés 2–60 napra állítható. Kézi frissítéshez használható a `hydroone_greenbutton.refresh` művelet.

## Belépési adatok és korlátok

Az email és a jelszó a Home Assistant szokásos konfigurációs bejegyzésében és annak biztonsági mentéseiben tárolódik. A kiegészítő HTTPS-en, kizárólag a Hydro One hivatalos webhelyeivel kommunikál. A böngésző munkamenetét és sütijeit nem másolja át. Nem használ külső közvetítő szolgáltatást.

Ez a myAccount-portálra épülő, nem hivatalos integráció. A Hydro One hivatalos **Connect My Data** szolgáltatásához jóváhagyott harmadik fél szükséges. A portál változása javítást igényelhet ebben a kiegészítőben. A verzió nem kezel CAPTCHA-t vagy többfaktoros belépési felszólítást.

Ha az első import sikertelen, a Home Assistant integrációoldala jelzi a hibát. Az általános üzenetek és a kiegészítő diagnosztikája nem tartalmaznak jelszót vagy teljes XML-t. Eltávolításhoz töröld az integráció bejegyzését, majd a saját mappáját és indítsd újra a Core-t. A kiegészítő eltávolításkor nem törli a korábbi Recorder-statisztikákat.

## Ellenőrzések és források

Az önálló tesztek Python 3.12 mellett futtathatók egy virtuális környezetben:

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

A Home Assistant a szükséges `aiohttp` csomagot már tartalmazza; az integráció nem kér új külső függőséget. A tesztek szimulált HTTP-válaszokat és szintetikus XML-t használnak, tehát nem bizonyítják az élő felhasználói belépést.

- [Hydro One Green Button](https://www.hydroone.com/saving-money-and-energy/green-button)
- [Hydro One harmadik fél alkalmazások](https://www.hydroone.com/savingmoneyandenergy_/Pages/third-party-apps.aspx)
- [Home Assistant Core 2026.9.2 Recorder](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/recorder/statistics.py)
- A Hydro One nyilvános portáljának aktuális kérései: `Customer`, `GetGreenButtonDownloadMyData`, valamint a `Settings/TivoliSignInUrl` belépési cím.
