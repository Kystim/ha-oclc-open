# OCLC OPEN Library for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Custom integration for public libraries whose online catalogue runs **OCLC OPEN**
(BIBLIOTHECA web OPAC, the "Mein Konto" pages on DotNetNuke), e.g.
[Stadtbücherei Iserlohn](https://opac.iserlohn.de).

> Status: early. Login, loans (including the renewability check), fees and card
> data are tested against the live Iserlohn catalogue. Reservations and the
> renewal itself follow the [opacclient](https://github.com/opacapp/opacclient)
> implementation and still need verification.

## Entities

| Entity | State | Attributes |
| --- | --- | --- |
| Loans | number of borrowed items | `loans` (title, author, due date, renewable, copy id …), `overdue`, `renewable`, `warning` |
| Next due date | earliest due date | `titles` due that day |
| Days until due | days until the earliest due date (negative when overdue) | |
| Reservations | pending reservations | `reservations` |
| Ready for pickup | reservations waiting at the library | `reservations` with pickup deadline |
| Fees | unpaid fees in EUR | fee lines, totals |
| Card valid until | expiry date of the library card | |
| Renew all (button) | renews every loan the library allows | |
| Calendar | on during an event; shows the next one | all-day events: returns (grouped per due date), pickup deadlines, card expiry |

## Calendar

The calendar (`calendar.iserlohn_library`, or `calendar.bucherei_iserlohn` in German) is
read-only and built from the account data; it makes no extra requests. Event titles
follow the Home Assistant language ("Rückgabe: 13 Medien" / "Return 13 items"). After a
renewal the return event moves with the next update. Returned items disappear: the
library only shows current loans.

Reminder two days before a return:

```yaml
automation:
  - alias: Library return reminder
    triggers:
      - trigger: calendar
        event: start
        entity_id: calendar.iserlohn_library
        offset: "-2:00:00:00"
    conditions:
      - condition: template
        value_template: "{{ 'Return' in trigger.calendar_event.summary or 'Rückgabe' in trigger.calendar_event.summary }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.calendar_event.summary }}"
          message: "{{ trigger.calendar_event.description }}"
```

## Renewal

- **Manual:** press *Renew all* or call `oclc_open.renew`, optionally with
  `copy_ids` from the loans attributes. The service returns per item whether the due
  date moved.
- **Automatic:** enable in the integration options. Loans due within the configured
  number of days are renewed once per due date. Refusals (reserved by someone else,
  renewal limit reached) create a persistent notification.
- Renewals that cost a fee are refused unless *Allow renewals that cost a fee* is on.
- Every attempt fires an `oclc_open_renewal` event (`title`, `renewed`,
  `old_due_date`, `new_due_date`, `automatic`).

## Installation (Marketplace)

Since Home Assistant 2026.11, HACS is built in as the *Marketplace*.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Kystim&repository=ha-oclc-open&category=integration)

After installing:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=oclc_open)

Or manually:

1. *Settings → Marketplace → Add from link* → `https://github.com/Kystim/ha-oclc-open`.
   Custom repositories need a one-time GitHub sign-in (read-only, public data).
2. Install *OCLC OPEN Library*.
3. *Settings → Devices & services → Add integration → OCLC OPEN Library*.
   Enter the catalogue URL, card number and password (Iserlohn default: date of birth
   as `TT.MM.JJJJ`).

Updates appear as update entities; installing an update needs a restart.

On Home Assistant before 2026.11 with standalone HACS: HACS → ⋮ → *Custom
repositories* → the URL above, type *Integration*, then restart after installing.

The device is named after the library ("Iserlohn library" / "Bücherei Iserlohn"),
so dashboard cards show names like "Iserlohn library Next due date".

## Development

```bash
uv run pytest
```

```bash
uv run python scripts/smoke.py --url https://opac.iserlohn.de
```

The smoke script logs in read-only and prints what the integration parses.
