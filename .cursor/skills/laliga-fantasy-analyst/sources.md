# Fuentes fiables

Usar al menos dos, cruzando con los datos MCP. Preferir hechos (convocatoria, parte médico, once oficial) sobre rumores.

## Prioridad alta

- [FútbolFantasy](https://www.futbolfantasy.com/) — onces, % titularidad, lesionados (`/laliga/lesionados`).
- Webs oficiales de clubes y [LaLiga](https://www.laliga.com/).
- [Relevo](https://www.relevo.com/) — periodismo de vestuario con contraste.

## Prioridad media (confirmar)

- [AS](https://as.com/), [Marca](https://www.marca.com/), [Mundo Deportivo](https://www.mundodeportivo.com/), [Sport](https://www.sport.es/).
- [BeSoccer](https://es.besoccer.com/) y [Transfermarkt](https://www.transfermarkt.es/) — plazo estimado de baja, no titularidad.

## Tarde de jornada

- Radio/webs de última hora (Cadena SER, Radio MARCA) y redes **oficiales** del club.
- El MCP `laliga_get_probable_lineups` y `laliga_get_injuries` cubren el corte previo.
- El XI de un rival en Fantasy se consulta con `laliga_get_lineup` / `laliga_get_rivals_teams` (snapshot `lineup/week/{jornada}`, igual que LaLigaApp). El XI editable (`/lineup` sin jornada) solo lo ve el dueño.

## No usar como única fuente

- Cuentas anónimas de X/Telegram, filtraciones sin segundo medio, hilos de foro.
