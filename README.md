# tata

![tata](docs/cover.jpg)

Ore e buste paga di lavoratrici e lavoratori domestici, in casa propria (self-hosted): un
**rapporto** per persona e assunzione, aperto o chiuso, anche più d'uno insieme (la colf e la
baby sitter). I rapporti chiusi restano consultabili per sempre.

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
| Tredicesima | una mensilità di retribuzione globale di fatto: con ore variabili, 1/12 del lordo effettivo dell'anno (ore, festività, ferie, permessi, malattia; esclusa l'indennità per ferie non godute) | art. 39 |
| TFR | retribuzione annua / 13,5 (in malattia conta la paga piena), rivalutato 1,5% + 75% ISTAT | art. 41, art. 2120 c.c. |
| Fascia INPS | paga oraria × 13/12 → contributo orario; importo fisso oltre 24 h settimanali | INPS circ. 9/2026 |
| Cassa Colf | 0,06 €/h, di cui 0,02 a carico del lavoratore | art. 54 |
| Deduzione | quota INPS del datore versata nell'anno, massimo 1.549,37 € | art. 10 c.2 TUIR |

Lo straordinario cade sui minuti più tardi della giornata o della settimana. Tutti i valori che
cambiano a gennaio stanno in [`rates/<anno>.yaml`](rates/2026.yaml), ciascuno con la sua fonte.

**Non gestito** (errore esplicito o fuori ambito): contratti a tempo determinato (contributo
addizionale), lavoratori conviventi (vitto e alloggio), assunzioni
precedenti al primo file di tariffe (2026). Il contratto non ha storico: un aumento o un nuovo
orario ricalcola tutti i mesi non ancora finalizzati, quindi conviene finalizzare ogni mese (in
ordine, l'app lo impone) appena pagato.

**Da verificare** sui testi ufficiali: la fascia INPS 2 (1,92 €/h) e l'importo oltre le 24 h
non compaiono nella notizia INPS ma in due fonti secondarie concordi, e tornano col resto della
tabella (contributo ≈ 20% del convenzionale in ogni fascia); le ore INPS dei giorni di malattia
sono le ore previste dall'orario in quei giorni.

## Il pulsante di timbratura

`/timbra` è una pagina con un solo grande pulsante, pensata per il telefono. Propone **Fine
turno** se c'è una timbratura aperta; **Conferma turno HH:MM-HH:MM** se si è oltre la metà di un
turno previsto non ancora registrato ed entro 30 minuti dalla sua fine; altrimenti **Inizio
turno**. Una timbratura aperta di un giorno precedente chiede l'orario di uscita. Il link
«registra a mano» porta al form *Registra* del mese.

Nella pagina del mese:

- **Cartellino**: le timbrature (cancellabili, inseribili a mano; oltre 8 h evidenziate). Alla
  chiusura di una timbratura il tempo fuori orario (oltre 5 minuti) diventa un evento EXTRA
  collegato; cancellando la timbratura sparisce anche quello. Cambiando l'orario nel contratto
  gli EXTRA delle timbrature dei mesi non finalizzati vengono ricalcolati. Non si registrano
  timbrature nel futuro (uscita dopo l'ora attuale).
- **Rimborso trasferte**: ogni turno timbrato (mattina o pomeriggio, al più due al giorno)
  porta il rimborso chilometrico dell'accompagnamento a scuola con l'auto della lavoratrice:
  km per turno × €/km delle tabelle ACI, da `TATA_RIMBORSO_KM` e `TATA_RIMBORSO_EURO_KM`
  (entrambe o nessuna). I km di un singolo turno si correggono dal form ✏️ della riga
  (sostituiscono quelli configurati). Trasferta fuori comune, quindi non imponibile (art. 51 c.5 TUIR): sta
  fuori dal lordo, dai contributi e dal TFR, e si aggiunge al netto nella riga *Da pagare*.
  L'auto si dichiara per turno, *auto propria* o *auto del datore* (che non rimborsa nulla):
  accanto al pulsante di *timbra*, a inizio turno, e nel form *Registra*; di default è
  quella della lavoratrice. Il cartellino è una tabella: ore, giorno, orario, auto, costo del
  tempo (minuti × paga oraria, senza maggiorazioni: un colpo d'occhio, non il prospetto) e
  costo della trasferta (solo con l'auto propria, una per turno); ✏️ carica la riga nel form
  *Registra* per correggerla, ❌ la cancella.
- **Si paga il timbrato**: il prospetto (sezione *Lavorato*) conta solo le ore timbrate, più
  festività, ferie, permessi, malattia e mandata a casa confermati. Niente timbrature, niente
  paga: i giorni ancora da venire e i vuoti non confermati non sono pagati. La sezione
  *Pianificato* mostra l'orario del contratto come se fosse lavorato, solo per confronto.
- **Da confermare**: i vuoti rispetto all'orario previsto (si contano dall'assunzione; ignorati i buchi fino a 5 minuti, le festività, i giorni di ferie o malattia). Per ognuno
  tre pulsanti: *assente* (ASSENZA), *permesso* (PERMESSO, pagato), *mandata a casa* (MORA:
  pagata come da orario, art. 1206 c.c., solo dentro l'orario). Un mese non ancora finito, con
  vuoti da confermare o con una timbratura aperta non si può finalizzare.

**Presenza dal wifi** (facoltativa): la pagina *presenza* del rapporto legge da Home Assistant la storia del
`device_tracker` del telefono della lavoratrice (ad esempio quello di
eve/openwrt-presence, che segna `not_home` 120 secondi dopo l'ultimo
aggancio) e propone le timbrature per turni (quelle che nessuna timbratura copre ancora
sono anche nella pagina del mese, con il loro *registra*):

- **mattina**: dal primo arrivo tra le 7:30 e le 8:15 all'ultima uscita prima di mezzogiorno,
  + 30 minuti se quell'uscita è entro le 8:30 (il tragitto per portare i bambini a scuola);
  un rientro dopo la scuola è assorbito e fa cadere i 30 minuti;
- **pomeriggio**: dalle 15:45 (uscita da scuola), o dall'arrivo se è prima, all'ultima uscita;
  gli arrivi dopo le 15:45 non spostano nulla.

Le uscite e i rientri in mezzo a un turno sono ignorati. Le eccezioni (malattia, orari
cambiati) non sono regole: la giornata che non torna (nessun arrivo tra 7:30 e 8:15, uscita
fuori turno, turno senza uscita, uscita ignota) è segnata *da controllare* e va registrata a
mano.

Niente viene registrato da solo: ogni proposta ha il suo pulsante *registra*, che passa dalla
stessa strada della timbratura a mano (validazione, EXTRA, vuoti da confermare). I riavvii di
Home Assistant (`unavailable`) non contano come uscite; una permanenza senza arrivo o uscita
nota (telefono già «in casa» a mezzanotte, uscita durante un riavvio) va registrata a mano.
In fondo, le ore timbrate per settimana. Si attiva con `TATA_HA_URL` e `TATA_HA_TOKEN`
(entrambe o nessuna) e, nel contratto di ogni rapporto, il tracker del telefono della
lavoratrice (`device_tracker...`). Il tracciamento del telefono di una dipendente
richiede un'informativa scritta.

**Installare sul telefono**: aprire `/timbra` e usare «Aggiungi a schermata Home»; si apre a
schermo intero (`static/manifest.webmanifest`, nessun service worker). `/static` e la pagina di
accesso sono pubblici, tutto il resto chiede di entrare.

**Da verificare** sul dispositivo: tata è raggiungibile solo dalla LAN (da fuori, VPN); la
sessione dura 90 giorni, quindi la PWA non chiede la password a ogni avvio.

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
cp .env.example .env     # cartelle, nome, token Cloudflare, rete, IP, password
mkdir -p /srv/tata/data /srv/tata/acme   # di proprietà di TATA_UID:TATA_GID
docker compose up -d --build
```

- **TLS**: certificato Let's Encrypt per `${TATA_DOMAIN}`, preso da `entrypoint.sh` con acme.sh
  al primo avvio (DNS-01 su Cloudflare: nessuna porta aperta verso internet) e tenuto in
  `${TATA_ACME}`. Il rinnovo si controlla una volta al giorno; uvicorn non ricarica i
  certificati, quindi dopo un rinnovo si ferma e `restart: unless-stopped` lo fa ripartire.
- **DNS**: solo sul DNS della LAN, un record A per `${TATA_DOMAIN}` che punta a `${TATA_IP}`;
  nessun record pubblico.
- **Accesso**: pagina di accesso con utente e password; la sessione è un cookie (`HttpOnly`,
  `Secure`, `SameSite=Lax`) con un token casuale di cui il database tiene solo l'hash, valido
  90 giorni o fino a *esci*, e decade se la password con cui si è entrati cambia. Il
  datore entra con `TATA_USER` e `TATA_PASSWORD` e vede
  tutti i rapporti (`/rapporti`). Dalla pagina del contratto può dare a ogni lavoratrice un
  nome utente suo (unico) e una password (salvata come hash scrypt): con quelli entra solo
  nel suo rapporto, in sola lettura sul mese e sul prospetto, e timbra (pulsante o a mano);
  tutto il resto le risponde 403. Le deduzioni dei contributi (`/anno/<anno>/deduzioni.yaml`)
  sommano tutti i rapporti, con un solo tetto annuo per datore.

## Ogni gennaio

1. Copiare `rates/<anno precedente>.yaml` in `rates/<anno>.yaml`.
2. Aggiornare i minimi CCNL (verbale della Commissione nazionale) e la tabella INPS (circolare
   di fine gennaio o inizio febbraio), ricontrollare ogni altro valore e le fonti nei commenti.
3. Durante l'anno, aggiungere in `tfr_coefficienti` i coefficienti ISTAT di rivalutazione del
   TFR (mese → % cumulata) man mano che escono; fino ad allora la pagina del TFR segnala la
   rivalutazione mancante.

## Licenza

MIT
