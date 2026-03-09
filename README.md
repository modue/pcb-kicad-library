# modue KiCad PCB Library

Wewnętrzna biblioteka KiCad 9 firmy modue. Zawiera symbole, footprinty i modele 3D komponentów używanych w produkcji.

Dostępna jako pakiet KiCad PCM — po zainstalowaniu biblioteki produkcyjne (`modue_PROD_*`) są od razu dostępne w KiCad bez konfliktów z wersjami developerskimi.

---

## Spis treści

1. [Struktura repozytorium](#struktura-repozytorium)
2. [Konfiguracja środowiska deweloperskiego](#konfiguracja-środowiska-deweloperskiego)
3. [Konwencje nazewnictwa](#konwencje-nazewnictwa)
4. [Dodawanie nowych komponentów](#dodawanie-nowych-komponentów)
5. [Wymagania dla symboli](#wymagania-dla-symboli)
6. [Walidacja](#walidacja)
7. [Tworzenie release'u](#tworzenie-releaseu)
8. [Instalacja przez KiCad PCM](#instalacja-przez-kicad-pcm)

---

## Struktura repozytorium

```
pcb-kicad-library/
├── symbols/                        # Biblioteki symboli KiCad
│   └── modue_MCU.kicad_sym
├── footprints/                     # Biblioteki footprintów
│   └── modue_QFN_DFN.pretty/
│       └── QFN-56-1EP_7x7mm_P0.4mm_EP4x4mm.kicad_mod
├── 3dmodels/                       # Modele 3D (STEP)
│   └── modue_DFN_QFN.3dshapes/
│       └── QFN-56-1EP_7x7mm_P0.4mm_EP4x4mm.step
├── resources/
│   └── icon.png                    # Ikona pakietu PCM (64×64 px)
├── docs/                           # Serwowane przez GitHub Pages
│   ├── repository.json
│   └── packages.json
├── scripts/
│   ├── build_release.py            # Skrypt budujący paczkę PCM
│   └── validate_symbols.py         # Skrypt walidujący biblioteki
├── .github/workflows/
│   ├── release.yml                 # Pipeline: walidacja + publikacja
│   └── validate-symbols.yml        # Pipeline: walidacja przy każdym pushu
└── metadata.json                   # Metadane pakietu PCM
```

---

## Konfiguracja środowiska deweloperskiego

### 1. Sklonuj repozytorium

```bash
git clone https://github.com/modue/pcb-kicad-library.git
```

### 2. Ustaw zmienną środowiskową KiCad

Footprinty referencują modele 3D przez zmienną `${KICAD_USER_MODUE_DIR}`. Musisz ją zdefiniować w KiCad przed pracą z biblioteką.

**KiCad → Preferences → Configure Paths → dodaj:**

| Nazwa zmiennej        | Wartość                                           |
|-----------------------|---------------------------------------------------|
| `KICAD_USER_MODUE_DIR` | `<ścieżka do katalogu nadrzędnego repozytorium>` |

Przykład — jeśli repozytorium jest w `~/Documents/modue/KiCad/pcb-kicad-library`, wartość zmiennej to `~/Documents/modue/KiCad`.

**Ścieżka modelu 3D w footprincie będzie wtedy:**
```
${KICAD_USER_MODUE_DIR}/pcb-kicad-library/3dmodels/modue_DFN_QFN.3dshapes/QFN-56.step
```

### 3. Dodaj biblioteki symboli do KiCad

**KiCad → Preferences → Manage Symbol Libraries → zakładka Global:**

| Nickname          | Library Path                                                      |
|-------------------|-------------------------------------------------------------------|
| `modue_MCU`       | `${KICAD_USER_MODUE_DIR}/pcb-kicad-library/symbols/modue_MCU.kicad_sym` |

### 4. Dodaj biblioteki footprintów do KiCad

**KiCad → Preferences → Manage Footprint Libraries → zakładka Global:**

| Nickname           | Library Path                                                               |
|--------------------|----------------------------------------------------------------------------|
| `modue_QFN_DFN`    | `${KICAD_USER_MODUE_DIR}/pcb-kicad-library/footprints/modue_QFN_DFN.pretty` |

---

## Konwencje nazewnictwa

### Biblioteki symboli

Format: `modue_<kategoria>.kicad_sym`

| Plik                     | Kategoria komponentów         |
|--------------------------|-------------------------------|
| `modue_MCU.kicad_sym`    | Mikrokontrolery               |

W paczce PCM symbole są automatycznie przemianowywane na `modue_PROD_<kategoria>.kicad_sym` (np. `modue_PROD_MCU.kicad_sym`). Dzięki temu zainstalowana biblioteka produkcyjna nie koliduje z wersją deweloperską otwartą w tym samym KiCad.

### Biblioteki footprintów

Format: `modue_<kategoria>.pretty/`

| Katalog                    | Kategoria komponentów  |
|----------------------------|------------------------|
| `modue_QFN_DFN.pretty/`    | Obudowy QFN i DFN      |

### Modele 3D

Format: `modue_<kategoria>.3dshapes/`

Nazwa pliku `.step` musi być **identyczna** jak nazwa footprintu (bez rozszerzenia).

| Katalog                      | Odpowiadający katalog footprintów |
|------------------------------|-----------------------------------|
| `modue_DFN_QFN.3dshapes/`    | `modue_QFN_DFN.pretty/`           |

Przykład:
```
footprints/modue_QFN_DFN.pretty/QFN-56-1EP_7x7mm_P0.4mm_EP4x4mm.kicad_mod
3dmodels/modue_DFN_QFN.3dshapes/QFN-56-1EP_7x7mm_P0.4mm_EP4x4mm.step
```

---

## Dodawanie nowych komponentów

### Nowy symbol

1. Otwórz KiCad Symbol Editor
2. Otwórz lub utwórz plik `symbols/modue_<kategoria>.kicad_sym`
3. Dodaj symbol i uzupełnij **wszystkie wymagane pola** (patrz niżej)
4. Zapisz plik

### Nowy footprint

1. Otwórz KiCad Footprint Editor
2. Otwórz lub utwórz bibliotekę `footprints/modue_<kategoria>.pretty/`
3. Utwórz footprint
4. Przypisz model 3D używając ścieżki:
   ```
   ${KICAD_USER_MODUE_DIR}/pcb-kicad-library/3dmodels/modue_<kategoria>.3dshapes/<nazwa>.step
   ```
5. Zapisz footprint

### Nowy model 3D

1. Umieść plik `.step` w katalogu `3dmodels/modue_<kategoria>.3dshapes/`
2. Nazwa pliku musi być **identyczna** jak nazwa footprintu

---

## Wymagania dla symboli

Każdy symbol (z wyjątkiem symboli zasilania: `#PWR`, `#FLG`) musi mieć wypełnione następujące pola:

| Pole           | Opis                                              | Przykład                        |
|----------------|---------------------------------------------------|---------------------------------|
| `Reference`    | Oznaczenie referencyjne                           | `U`                             |
| `Value`        | Wartość / nazwa komponentu                        | `ESP32-S3`                      |
| `Footprint`    | Przypisany footprint (format `Biblioteka:Nazwa`)  | `modue_QFN_DFN:QFN-56-...`      |
| `Datasheet`    | Link do datasheet                                 | `https://...`                   |
| `Description`  | Krótki opis komponentu                            | `Wi-Fi + BT MCU, 240 MHz`       |
| `MPN`          | Manufacturer Part Number                          | `ESP32-S3-WROOM-1-N8`           |
| `Manufacturer` | Nazwa producenta                                  | `Espressif`                     |
| `IPN`          | Internal Part Number (wewnętrzny numer katalogowy)| `IC-0042`                       |

Pole `Footprint` musi referencować footprint **istniejący w tym repozytorium** — walidacja sprawdza to automatycznie.

---

## Walidacja

Walidacja uruchamia się automatycznie przy każdym pushu zawierającym pliki `.kicad_sym`.

### Co jest sprawdzane

**1. Właściwości symboli** — każdy symbol musi mieć wypełnione wszystkie wymagane pola (patrz tabela wyżej).

**2. Referencje footprintów** — pole `Footprint` każdego symbolu musi wskazywać na footprint istniejący w `footprints/`. Sprawdzane gdy `footprints/` zawiera pliki `.kicad_mod`.

**3. Modele 3D footprintów** — każdy footprint w `footprints/` musi mieć odpowiadający plik `.step` o tej samej nazwie w `3dmodels/`. Sprawdzane gdy `footprints/` zawiera pliki `.kicad_mod`.

### Uruchomienie lokalne

```bash
# Walidacja wszystkich plików (tryb ścisły)
python scripts/validate_symbols.py --strict

# Walidacja konkretnego pliku
python scripts/validate_symbols.py --strict symbols/modue_MCU.kicad_sym

# Tryb ostrzeżeń (nie blokuje, tylko raportuje)
python scripts/validate_symbols.py --warn-only symbols/modue_MCU.kicad_sym
```

---

## Tworzenie release'u

Release jest tworzony przez GitHub Actions automatycznie po dodaniu tagu w formacie `vYY.MMDD.N`.

### Format tagu

```
vYY.MMDD.N
```

| Segment | Znaczenie                        | Przykład     |
|---------|----------------------------------|--------------|
| `YY`    | Rok (2 cyfry)                    | `26`         |
| `MMDD`  | Miesiąc i dzień (bez zer wiodących) | `309` (9 marca) |
| `N`     | Numer release'u w danym dniu     | `1`, `2`, `3`|

Przykłady: `v26.309.1`, `v26.309.2`, `v26.1224.1`

### Procedura release'u

```bash
# 1. Upewnij się że wszystkie zmiany są zacommitowane i spushowane
git status

# 2. Utwórz i wypchnij tag
git tag v26.309.1
git push origin v26.309.1
```

### Co robi pipeline release'u

1. **Walidacja** — uruchamia `validate_symbols.py --strict` na wszystkich plikach. Jeśli walidacja nie przejdzie, release nie zostanie opublikowany.
2. **Budowanie ZIP** — pakuje biblioteki do archiwum zgodnego z KiCad PCM:
   - Symbole są przemianowywane: `modue_X.kicad_sym` → `modue_PROD_X.kicad_sym`
   - Ścieżki modeli 3D w footprintach są przepisywane na ścieżki PCM
3. **Publikacja** — tworzy GitHub Release z plikiem `library.zip`
4. **Aktualizacja indeksu** — aktualizuje `docs/packages.json` i `docs/repository.json` (serwowane przez GitHub Pages)

### Transformacja ścieżek 3D podczas pakowania

| Środowisko       | Ścieżka w footprincie                                                                           |
|------------------|-------------------------------------------------------------------------------------------------|
| Deweloperskie    | `${KICAD_USER_MODUE_DIR}/pcb-kicad-library/3dmodels/modue_DFN_QFN.3dshapes/model.step`        |
| PCM (po paczce)  | `${KICAD_USER_TEMPLATE_DIR}/../3rdparty/3dmodels/com.github.modue.pcb-kicad-library/modue_DFN_QFN.3dshapes/model.step` |

---

## Instalacja przez KiCad PCM

### Jednorazowa konfiguracja repozytorium

1. Otwórz KiCad → **Plugin and Content Manager**
2. Przejdź do zakładki **Repositories**
3. Kliknij **`+`** i wklej URL:
   ```
   https://modue.github.io/pcb-kicad-library/repository.json
   ```
4. Kliknij **OK**

### Instalacja biblioteki

1. Otwórz KiCad → **Plugin and Content Manager**
2. Kliknij **Refresh**
3. Znajdź **modue KiCad PCB Library**
4. Kliknij **Install**

Po instalacji biblioteki będą dostępne pod nazwami z prefiksem `PROD`:
- Symbole: `modue_PROD_MCU`, `modue_PROD_<kategoria>`
- Footprinty: `modue_QFN_DFN`, `modue_<kategoria>` (bez zmiany nazwy)
