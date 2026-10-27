# Built-in Personas — Design

Status: draft for approval (2026-10-25)

## Intent

CastStudio's briefings are spoken by two unnamed "roles" today: a Host
(voice Pulcherrima, style `news_anchor`) and a Reader (voice Enceladus, style
`life_coach`). Each is only a voice plus a delivery style; the words are
always written in CastStudio's one neutral voice. A person can override the
voice and style by typing them into the person's Advanced settings.

The user built rich personas in Google AI Studio (a character, a base voice
and a delivery prompt) and found they can only be reproduced in Home
Assistant by hand-building conversation agents and scripts. Most CastStudio
users will never do that. CastStudio should ship a **cast of built-in
personas** that shape both **how the briefing sounds** and **how it is
worded**, so anyone can choose a persona from a list.

## Goals

- A built-in catalog of personas. Each persona has:
  - a stable id and a display name (used only in the settings UI),
  - a **character**: a short description that shapes the written words,
  - a **Gemini voice** (one of the 30 Gemini TTS voices),
  - a **delivery prompt**: how the voice performs (tempo, tone, breathing),
  - an **OpenAI fallback voice** (one of alloy, echo, fable, onyx, nova,
    shimmer) for people on OpenAI TTS,
  - the **roles it suits**: Host, Reader, or both.
- Today's two roles become two named default personas ("Default Host" and
  "Default Reader") whose voice and delivery match today's output, so a
  person who changes nothing hears the same briefing as before.
- Per person, for each role (Host, Reader), the person can choose:
  - nothing: today's behaviour exactly, including any voice or style they
    typed into Advanced;
  - one persona: always that persona;
  - several personas: CastStudio picks one at random.
- The pick list for a role offers only the personas suited to that role, so
  a high-energy host can't be put on the Bible reading.
- The persona shapes the voice and the wording only. It **never** names
  itself: no "Arthur here", no persona name on the display card.

## Roles (unchanged from today)

- **Host** speaks: greeting, weather, agenda, commute, fun fact, national
  day, closing, and the "that's everything" (all-heard) clip.
- **Reader** speaks: scripture (daily text) and daily encouragement.

## The cast (v1)

| id | Name | Character (summary) | Gemini voice | Roles |
|---|---|---|---|---|
| default_host | Default Host | Today's clear, friendly news-anchor host | Pulcherrima | Host |
| default_reader | Default Reader | Today's calm, warm life-coach reader | Enceladus | Reader |
| elena | Elena | Warm, grounded young Saint Lucian woman; calm, affectionate, gentle humour | Kore | Host, Reader |
| malenfant | Malenfant | Mature, pastoral mentor; earnest, dignified, reflective | Charon | Reader |
| della | Della | Joyful Southern auntie in her forties; soothing, uplifting, homey | Kore | Host, Reader |
| cole | Cole | Educated, well-read Brooklyn man in his thirties; composed, stylish, dry wit | Charon | Host |
| arthur | Arthur | Crisp British butler; precise, unflappable, dry wit | Iapetus | Host |
| theo | Theo | Upbeat morning-radio host; bright and energising | Puck | Host |
| nana_rose | Nana Rose | Gentle grandmother; tender, unhurried | Sulafat | Host, Reader |
| sage | Sage | Calm meditation guide; spacious, peaceful | Achernar | Reader |
| ray | Ray | No-nonsense news and traffic anchor; facts first, brisk | Alnilam | Host |
| pip | Pip | Playful, kid-friendly guide; simple words, encouraging | Leda | Host |

Della is an original character: the Southern-auntie style is kept but she
must not reference or imitate any real person. No shipped persona may name
or describe a real person, or any detail of the author's household.

The character and delivery text for Elena, Malenfant, Della and Cole start from the prompts the user made in AI
Studio, with household and real-person references removed. The builder
writes the rest in the same style. Each character text must end with the
spoken-output rules the briefing already relies on (no stage directions, no
markdown or emojis, numbers said as spoken) and must not tell the writer
to introduce itself.

## How a briefing gets its personas

- Each role resolves to **one persona per person per briefing day**. A
  random pick is stable for that day (for example seeded from the person,
  role and briefing day), so the prebuild and any later play or rebuild that
  day agree, and the voice doesn't change between segments. The next
  briefing day may pick someone else.
- The resolved Host persona's character is given to the writer for every
  Host segment, and the Reader persona's for every Reader segment. The
  writer prompt already has a "Voice Personality" line; the persona's
  character replaces it there.
- The resolved persona's delivery prompt goes to Gemini TTS as the full
  director profile, passed through unchanged (not wrapped in the
  "Style: …, natural, clear, and expressive" fallback used for unknown
  personality text).
- On OpenAI TTS the persona's OpenAI fallback voice is used.

## Settings UI

- In the person form, add two multi-select fields, **Host personas** and
  **Reader personas**, each listing only the personas suited to that role.
  Empty means today's behaviour.
- When a role has personas selected, they take precedence over the
  existing Advanced voice and personality text fields for that role. Those
  fields stay for people who pick no persona.

## Constraints

- Public HACS repo: no personal names, places, zone ids, sensors or
  household details in the catalog, code, tests or commits. Tests use fake
  values (Alex, Sam, `media_player.kitchen_speaker`).
- No behaviour change for a person who selects no personas.
- A persona change takes effect the same way any other person setting
  does today: on the next prebuild or the next briefing day. Stored
  segments are only judged fresh by briefing day and max age; there is no
  settings fingerprint, and v1 does not add one. *(Builder-check call.)*

## Non-goals (v1)

- User-created personas. The catalog lives in one module the app reads as
  data, so user personas (for example a "Persona" subentry) can join the
  same list later without reworking the rest.
- Per-segment persona choice beyond the two roles.
- The persona introducing itself or appearing on the display.
- Event announcements (door opened, arrivals). These are a future feature;
  the catalog should be reusable by it, nothing more.
- Choosing which AI writes the briefing (the "AI writer per person" idea).

## Decisions from the builder check

These were settled after review; items marked *call* are the designer's
call, not the user's, and can be revisited.

- **Briefing day** is the existing `briefing_day(person, now)`, which rolls
  over at the person's reset time. Picks are independent per person.
- **Where the character applies:** wherever the AI writes the words. This
  includes the conversation-agent path, since both paths use the same
  writer prompt. Fixed text, such as the verbatim daily-text reading, keeps
  its words and only takes the persona's voice and delivery. No new AI
  calls are added to segments that don't have one today. *(call)*
- **Two separate strings:** the step carries the persona's character for
  the writer and its delivery prompt for TTS as separate keys. The TTS
  path gets an explicit way to receive a full director profile that skips
  the `PERSONALITY_PROMPTS` lookup and the "Style: …" wrapper. Today's
  `voice_personality` path stays for people with no persona. *(call)*
- **Other TTS engines:** on OpenAI TTS the persona's own OpenAI voice is
  used directly, bypassing the Gemini-name mapping. On HA TTS fallback the
  delivery prompt is dropped; the character still shapes the words.
  *(call)*
- **Advanced fields:** a selected persona simply overrides that role's
  typed voice and personality, with no warning or migration. *(call)*
- **Unknown persona ids** (for example one removed from a later catalog)
  are dropped silently. If none remain, the role falls back to today's
  behaviour. *(call)*
- **Defaults:** Default Host and Default Reader reuse the existing
  `news_anchor` and `life_coach` delivery profiles, so they can't drift
  from today's output. They can be selected like any other persona,
  including as part of a random set. *(call)*

## Left to the builder

- Exact character and delivery wording for the new personas, the OpenAI
  fallback voice for each, and how the catalog is laid out in code.
- How the daily random pick is seeded, as long as it is stable for a
  person, role and briefing day.
- How "stale because the persona changed" is detected, consistent with how
  stored segments are already invalidated.
