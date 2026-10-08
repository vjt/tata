# tata

![tata](docs/cover.jpg)

Ore e buste paga di **una** lavoratrice o lavoratore domestico, in casa propria (self-hosted).

Pensata per una famiglia che assume una baby sitter a ore con il CCNL lavoro domestico del
28 ottobre 2025. Si registrano solo le eccezioni all'orario settimanale; tata produce:

- il **prospetto paga** mensile (PDF con due riquadri per le firme), CCNL art. 34;
- i **contributi INPS e Cassa Colf** di ogni trimestre, con le scadenze (10 aprile, 10 luglio,
  10 ottobre, 10 gennaio);
- **tredicesima**, saldi di **ferie** e **permessi**, fondo **TFR** con rivalutazione ISTAT e
  anticipi, liquidazione a fine rapporto;
- l'**attestazione annuale delle retribuzioni** per il 730 del lavoratore (CCNL art. 34 c.6);
- un **YAML dei contributi deducibili** del datore (rigo E23 / RP23, principio di cassa, con
  il tetto di legge).

Il datore di lavoro domestico non è sostituto d'imposta: niente ritenute IRPEF, niente CU,
niente 770.

## Regole applicate

| Voce | Regola | Fonte |
|---|---|---|
| Lavoro notturno 22–6 | +20% | CCNL art. 14 c.6 |
| Straordinario (oltre 8 h/giorno o 40 h/settimana) | +25% di giorno, +50% di notte; tra 40 e 44 h settimanali diurne +10% | art. 15 |
| Domenica o festività lavorate | +60% | art. 13 c.4, art. 16 c.3 |
| Festività | ogni festività del mese pagata 1/6 dell'orario settimanale, lavorativa o no | art. 16 c.2 |
| Ferie | 26 giorni lavorativi l'anno (lunedì–sabato), ciascuno pagato 1/6 dell'orario settimanale | art. 17 |
| Permessi retribuiti | 12 h l'anno da 30 h settimanali in su, in proporzione sotto e per mesi di servizio | art. 19 |
| Malattia | 8/10/15 giorni pagati su 365 in base all'anzianità; 50% fino al 3° giorno consecutivo, poi 100%; 1/30 della paga mensile per giorno di calendario | art. 27, chiarimento 2 |
| Scatti di anzianità | +4% del minimo ogni biennio, dal mese successivo, massimo 7 | art. 37 |
| Tredicesima | una mensilità (orario settimanale × 52/12), 1/12 per ogni mese con almeno 15 giorni di servizio | art. 39, chiarimento 4 |
| TFR | retribuzione annua / 13,5 (in malattia conta la paga piena), rivalutato 1,5% + 75% ISTAT | art. 41, art. 2120 c.c. |
| Fascia INPS | paga oraria × 13/12 → contributo orario; importo fisso oltre 24 h settimanali | INPS circ. 9/2026 |
| Cassa Colf | 0,06 €/h, di cui 0,02 a carico del lavoratore | art. 54 |
| Deduzione | quota INPS del datore versata nell'anno, massimo 1.549,37 € | art. 10 c.2 TUIR |

Lo straordinario cade sui minuti più tardi della giornata o della settimana. Tutti i valori che
cambiano a gennaio stanno in [`rates/<anno>.yaml`](rates/2026.yaml), ciascuno con la sua fonte.

**Non gestito** (errore esplicito o fuori ambito): contratti a tempo determinato (contributo
addizionale), lavoratori conviventi (vitto e alloggio), più di un lavoratore, assunzioni
precedenti al primo file di tariffe (2026). Il contratto non ha storico: un aumento o un nuovo
orario ricalcola tutti i mesi non ancora finalizzati, quindi conviene finalizzare ogni mese (in
ordine, l'app lo impone) appena pagato.

**Da verificare** sui testi ufficiali: la fascia INPS 2 (1,92 €/h) e l'importo oltre le 24 h
vengono da una fonte secondaria; le ore INPS dei giorni di malattia sono le ore previste
dall'orario in quei giorni.

## Privacy: codice pubblico, dati mai

Nomi, codici fiscali, ore e buste paga stanno in `TATA_DATA_DIR`, un volume fuori dal
repository. `tests/test_repo_hygiene.py` fallisce se viene tracciato un database, un PDF o un
`.env`, o se un file tracciato contiene un codice fiscale diverso da quelli inventati dei test.

## Sviluppo

Il container è l'unico ambiente: sull'host non si installa niente.

```bash
git config core.hooksPath .githooks   # il pre-commit lancia scripts/check.sh
scripts/test.sh                       # pytest
scripts/lint.sh                       # ruff + pyright strict
scripts/format.sh                     # ruff --fix + format
```

Le regole di progetto (niente argomenti di default, soldi in Decimal, tempo in minuti interi,
solo modelli Pydantic, calcoli senza I/O) sono in [CLAUDE.md](CLAUDE.md) e le fa rispettare
`tests/test_architecture.py`.

## Installazione

Un container con un proprio indirizzo sulla LAN, che termina il TLS da sé sulla porta 443.

```bash
cp .env.example .env     # cartella dati, cartella certificati, rete, IP, password
mkdir -p /srv/tata/data /srv/tata/certs   # di proprietà di TATA_UID:TATA_GID
docker compose up -d --build
```

- **TLS**: `server.crt` e `server.key` in `${TATA_CERTS}`. uvicorn li legge all'avvio, quindi
  chi li rinnova deve riavviare il container. Con un cert pusher per step-ca che distribuisce ai
  container Docker copiando i file e mandando un segnale, il target è:

  ```ini
  [docker:tata]
  container_name = tata
  hostname = tata.example.lan
  cert_dest = /root/tata/server.crt
  key_dest = /root/tata/server.key
  signal = RESTART
  ```

  con `${TATA_CERTS}` montato su `/root/tata` nel pusher.
- **DNS**: un record A per il nome host che punta a `${TATA_IP}`.
- **Accesso**: HTTP Basic, nome utente qualsiasi, password da `TATA_PASSWORD`.

## Ogni gennaio

1. Copiare `rates/<anno precedente>.yaml` in `rates/<anno>.yaml`.
2. Aggiornare i minimi CCNL (verbale della Commissione nazionale) e la tabella INPS (circolare
   di fine gennaio o inizio febbraio), ricontrollare ogni altro valore e le fonti nei commenti.
3. Durante l'anno, aggiungere in `tfr_coefficienti` i coefficienti ISTAT di rivalutazione del
   TFR (mese → % cumulata) man mano che escono; fino ad allora la pagina del TFR segnala la
   rivalutazione mancante.

## Licenza

MIT
