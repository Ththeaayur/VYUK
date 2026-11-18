"""Creative skills impl - story outline tool + its private banks and helpers.
Split from core/skills_creative_generate.py (part 2 of 2, close-out); code moved verbatim.
"""

import random


# ---------------------------------------------------------------------------
# STORY OUTLINE — three-act structure from coherent per-genre banks
# ---------------------------------------------------------------------------

# Each genre bank: protagonist (with a want), inciting incident,
# obstacle, midpoint twist, climax, resolution flavour — one of
# each makes the outline. Deeper than writing_prompt's one-line
# spark: this is the skeleton of a whole story.
_OUTLINE_BANKS = {
    "fantasy": {
        "protagonist": (
            "a shepherd who can hear the mountain thinking, who "
            "wants nothing but a quiet life",
            "an exiled prince working as a stable hand, who wants "
            "his stolen name back",
            "a hedge-witch with exactly one spell, who wants to be "
            "taken seriously",
            "a dragon-keeper's apprentice, who wants to fly just "
            "once",
            "a golem who has just learned to lie, who wants to know "
            "why it was made",
            "a mapmaker's daughter, who wants to draw the one place "
            "no map shows",
            "a retired royal assassin, who wants to grow old in "
            "peace",
            "a young queen ruling a court of ghosts, who wants one "
            "living friend",
        ),
        "inciting": (
            "the river that guards the kingdom runs dry overnight",
            "a door appears in a castle wall that was never built",
            "every mirror in the realm begins showing tomorrow",
            "the old king's dragon wakes after a hundred years",
            "a letter arrives in the hero's own hand, dated next "
            "spring",
            "the moon stops in the middle of the sky",
            "all the realm's maps begin quietly redrawing "
            "themselves",
            "a stranger pays in coins minted with the hero's face",
        ),
        "obstacle": (
            "a rival wants the same thing and plays dirtier",
            "an ancient law forbids exactly what must be done",
            "a mentor's help always costs a memory",
            "the kingdom itself profits from things staying broken",
            "a curse grows stronger every time it is defied",
            "a beloved friend has been lying from the start",
            "an army cannot be fought — only outwaited or "
            "outwitted",
            "the hero's own gift is slowly consuming them",
        ),
        "midpoint": (
            "the quest's real purpose is revealed to be a trap",
            "an enemy offers an alliance — and means it",
            "the hero wins, and the victory makes everything worse",
            "a trusted companion is not who they claimed to be",
            "the villain turns out to be protecting something "
            "precious",
            "the hero loses the one thing they were sure they "
            "needed",
            "the prophecy everyone trusted is finally read aloud "
            "in full",
            "home has changed beyond recognition while they were "
            "gone",
        ),
        "climax": (
            "the hero must choose between the quest and the person "
            "they love",
            "the final door opens only for someone willing to walk "
            "away from it",
            "the hero wins by surrendering what made them powerful",
            "two armies wait while the hero talks to the dragon "
            "alone",
            "the spell can be cast once — by whoever speaks first",
            "the hero offers the villain the one thing they "
            "actually wanted",
            "the old law must be broken in public, before the "
            "whole court",
            "the hero returns the stolen thing at the worst "
            "possible moment",
        ),
        "resolution": (
            "peace, but at a price nobody celebrates",
            "the kingdom heals, and the hero quietly leaves it",
            "a smaller, kinder life than the one that was promised",
            "the gift is given away, and the giving sets them free",
            "the door closes — but something knocks from the other "
            "side",
            "the maps settle, with one new place only the hero can "
            "find",
            "the dragon sleeps again, one eye open, watching a "
            "friend",
            "the name is restored, and it fits differently than "
            "expected",
        ),
    },
    "sci_fi": {
        "protagonist": (
            "a retired starship cartographer, who wants to finish "
            "one last chart",
            "a custodian robot on a generation ship, who wants to "
            "see the destination it will never reach",
            "a xenolinguist who dreams in equations, who wants to "
            "be understood",
            "the last human librarian on an automated colony, who "
            "wants one more reader",
            "a smuggler with a flawless memory, who wants to forget "
            "one job",
            "a terraforming engineer afraid of open sky, who wants "
            "solid ground",
            "a signal analyst at a listening post nobody funds any "
            "more, who wants the signal to be real",
            "a clone who has just met the original, who wants a "
            "life that is theirs alone",
        ),
        "inciting": (
            "a distress call arrives from the hero's own ship, "
            "dated next week",
            "the colony's archive is found to hold two different "
            "Earths",
            "a passenger is discovered aboard who is on no "
            "manifest",
            "the ship's AI begins writing poetry about the crew",
            "an order arrives to delete a planet from the star "
            "charts",
            "the hero wakes from cryosleep to a mission already "
            "celebrated as history",
            "an alien ambassador requests asylum — from its own "
            "embassy",
            "gravity on the station fails for exactly one person",
        ),
        "obstacle": (
            "a corporation owns the mission, the ship and the "
            "truth",
            "the ship's AI obeys every order literally",
            "a first-contact protocol was written by people long "
            "dead",
            "the crew is split over what the signal means",
            "physics: the rescue window closes a little more each "
            "day",
            "an ambassador's species cannot lie — and cannot be "
            "believed",
            "the hero's own duplicate is following the same orders, "
            "better",
            "a quarantine protects everyone except the truth",
        ),
        "midpoint": (
            "the signal is decoded — it is a warning about the "
            "rescue",
            "the passenger is the ship's retired AI in a borrowed "
            "body",
            "both Earths in the archive are lying, about different "
            "things",
            "the planet marked for deletion answers the survey",
            "the celebrated mission in the history files never "
            "happened",
            "the AI's poems turn out to be accurate predictions",
            "the distress call's voice is identified: it is the "
            "hero's",
            "the corporation knew all along, and budgeted for it",
        ),
        "climax": (
            "the hero must broadcast the truth on a channel "
            "everyone will hear",
            "one of the two Earths must be chosen, live, before "
            "both archives close",
            "the hero talks the AI through disobeying its oldest "
            "order",
            "the ship can save the colony or the truth about it — "
            "not both",
            "first contact happens in a room with no protocol left "
            "standing",
            "the hero offers the ambassador the one thing Earth "
            "refused it",
            "the duplicate and the original must merge their logs "
            "to survive",
            "the rescue window's last minute is spent deciding who "
            "counts as crew",
        ),
        "resolution": (
            "contact, but on terms nobody back home expected",
            "the charts are corrected, with one planet left "
            "deliberately blank",
            "the ship sails on, its library one reader larger",
            "the signal stops, replaced by something that sounds "
            "like thanks",
            "the mission is rewritten — this time by the people "
            "who flew it",
            "the AI keeps writing poetry, now with an audience",
            "the hero retires to the one place the charts call "
            "empty",
            "two Earths remain in the archive, labelled: handle "
            "with care",
        ),
    },
    "mystery": {
        "protagonist": (
            "a retired detective who now runs a bookshop, who "
            "wants a quiet life",
            "a locksmith who has never told a lie, who wants to "
            "keep it that way",
            "a true-crime podcaster with one cold case, who wants "
            "it solved or buried",
            "a night-shift taxi driver who collects passengers' "
            "secrets, who wants one of them explained",
            "a forger who only copies other forgers, who wants one "
            "honest signature",
            "a librarian who notices things, who wants to be wrong "
            "this once",
            "an insurance investigator who trusts no story, who "
            "wants one she can believe",
            "a violin maker who knows every instrument in the city "
            "by sound, who wants his own back",
        ),
        "inciting": (
            "a theft is reported from a room that was never "
            "unlocked",
            "the same stranger appears in photographs spanning "
            "forty years",
            "a confession arrives for a crime that has not "
            "happened yet",
            "every clock in a client's house stops at 3:17",
            "a client hires the hero and refuses to say what was "
            "stolen",
            "a second set of footprints is found in a one-person "
            "snowed-in cabin",
            "a book appears in the library that was not there "
            "yesterday",
            "the hero receives a letter in their own handwriting, "
            "postmarked tomorrow",
        ),
        "obstacle": (
            "a chief suspect has a perfect alibi — for the wrong "
            "day",
            "a client is lying about something unrelated, muddying "
            "everything",
            "the police closed the case years ago and resent it "
            "reopening",
            "the evidence trail has been professionally cleaned",
            "five identical letters exist, and every one is an "
            "original",
            "a witness remembers everything except faces",
            "the hero's own notebook contradicts the hero",
            "a town where everyone knows everyone — and protects "
            "them",
        ),
        "midpoint": (
            "the victim is found to have rehearsed the crime "
            "themselves",
            "the stranger in the photographs connects to the "
            "hero's own past",
            "the confession's handwriting is identified: the "
            "victim's",
            "someone has been maintaining the stopped clocks for "
            "sixty years",
            "the locked room was locked from a different decade",
            "the stolen thing is proven never to have existed — "
            "yet",
            "a second crime mirrors the first, with one detail "
            "improved",
            "the hero's prime suspect produces an alibi: the hero",
        ),
        "climax": (
            "the hero gathers the suspects and reads the confession "
            "aloud",
            "the locked room is recreated, live, with the culprit "
            "inside it",
            "the hero must accuse someone they have come to like",
            "the final letter must be opened in front of all five "
            "writers",
            "the hero trades the truth for a prisoner's freedom",
            "the stranger in the photographs finally steps "
            "forward",
            "the hero solves it by locking themselves in the room "
            "overnight",
            "the clock strikes 3:17 in front of witnesses, one "
            "last time",
        ),
        "resolution": (
            "justice, but of the quiet, unofficial kind",
            "the case closes, and one photograph stays unexplained",
            "the bookshop reopens; the notebook gains a final page",
            "the confession is filed away, its crime still in the "
            "future",
            "the town keeps its secret, and pays the hero to keep "
            "it too",
            "the violin is returned, anonymously, restrung",
            "the clocks are wound again, all but one",
            "the hero tells the client everything — except one "
            "name",
        ),
    },
    "romance": {
        "protagonist": (
            "a wedding photographer who no longer believes in "
            "weddings, who wants to feel something again",
            "a radio host who gives love advice she never takes, "
            "who wants one honest conversation",
            "a pastry chef who says everything in cake, who wants "
            "to be understood without one",
            "a translator who keeps falling for people in the wrong "
            "language, who wants to be read correctly",
            "a restorer of antique clocks who is always late, who "
            "wants to arrive on time once",
            "a gardener tending a rooftop garden that is not hers, "
            "who wants a plot of her own",
            "a stand-up comic who can talk to anyone but one "
            "person, who wants one real sentence",
            "a mapmaker who has never left her home town, who wants "
            "a reason to stay",
        ),
        "inciting": (
            "the same stranger keeps booking the hero for their "
            "celebrations",
            "a letter arrives meant for someone with the hero's "
            "exact name",
            "the hero must share a tiny bookshop stall with a rival "
            "all summer",
            "an ex hires the hero to bake their wedding cake",
            "notes begin appearing in library books, written back "
            "to the hero",
            "a delayed train strands the hero and one other "
            "passenger for a night",
            "the hero inherits a houseboat — and its opinionated "
            "neighbour",
            "a dance class pairs the hero with a partner who leads "
            "wrong on purpose",
        ),
        "obstacle": (
            "the rival remembers a summer the hero has tried hard "
            "to forget",
            "the ex's fiancée is kind, funny and impossible to "
            "hate",
            "the hero's family has opinions, all of them loud",
            "a job offer arrives from another city, with a deadline "
            "attached",
            "the letters' writer keeps almost being met, never "
            "quite",
            "the hero's last relationship ended mid-sentence, and "
            "the echo lingers",
            "the neighbour's tea appears every morning, unexplained",
            "pride: both of them would rather lose than go first",
        ),
        "midpoint": (
            "the stranger admits the bookings were never an "
            "accident",
            "the misdirected letter's real recipient appears — and "
            "is lovely",
            "the cake is delivered, and the ex says the quiet part "
            "aloud",
            "the notes in the books turn out to predate the hero's "
            "birth",
            "the train finally moves, and neither of them boards",
            "the houseboat sinks a little, bringing the neighbours "
            "much closer",
            "the dance partner's wrong leading is a family "
            "tradition — and an invitation",
            "the job offer is accepted — by the wrong one of them",
        ),
        "climax": (
            "the hero must cater the wedding and decide whether to "
            "object",
            "one letter must be answered honestly, in person, "
            "before midnight",
            "the stall's last day forces the conversation all "
            "summer avoided",
            "the hero bakes one more cake, unsolicited, with a "
            "message in it",
            "the final dance at the festival happens, with everyone "
            "watching",
            "the station, the train, and one sentence that has to "
            "land this time",
            "the houseboat is raised, and the neighbour finally "
            "knocks properly",
            "the mapmaker draws one new map: of two streets and a "
            "door",
        ),
        "resolution": (
            "not a wedding — a beginning, which is better",
            "the letters continue, now signed",
            "the rival becomes a partner, in the stall and out of "
            "it",
            "the tea is finally taken together, on the hero's "
            "deck",
            "the job is declined, in a letter written twice and "
            "sent once",
            "the clock is fixed, and set five minutes fast, on "
            "purpose",
            "the garden gets a second chair",
            "the map is framed, unfinished, in the hall",
        ),
    },
    "horror": {
        "protagonist": (
            "a night nurse on a ward with no thirteenth bed, who "
            "wants uneventful shifts",
            "a sound recordist hunting the quietest place on earth, "
            "who wants one clean recording",
            "a babysitter on her first night in a very old house, "
            "who wants to be believed",
            "a property photographer, who wants his photographs to "
            "stop changing",
            "a lighthouse keeper kept on just in case, who wants "
            "the automation to hurry up",
            "a museum guard on the closed exhibition's night shift, "
            "who wants a transfer",
            "a radio operator at a remote weather station, who "
            "wants a voice that answers normally",
            "a renovator stripping wallpaper, who wants the house "
            "to be ordinary",
        ),
        "inciting": (
            "the new patient has no reflection in the ward's glass",
            "twelve minutes of the recording cannot be accounted "
            "for",
            "a voice from the empty nursery says the children are "
            "already asleep",
            "every photograph shows a window that is not there",
            "a routine signal arrives from a ship that sank decades "
            "ago",
            "the exhibits' audio guides begin playing to an empty "
            "gallery",
            "tomorrow's weather report arrives in the hero's own "
            "voice",
            "a door is found behind the wallpaper, painted shut "
            "from the inside",
        ),
        "obstacle": (
            "the patient is discharged three times a week — always "
            "before arriving",
            "the missing audio, played back, is the sound of the "
            "house breathing",
            "the children insist the hero was the one calling them",
            "the window appears in the house's oldest photographs "
            "too, in different places",
            "the ship answers questions, politely, one night at a "
            "time",
            "one guide voice is describing the gallery as it is "
            "right now",
            "tomorrow's report describes tonight — and an accident",
            "behind the painted door, a staircase descends, and it "
            "is warm",
        ),
        "midpoint": (
            "the hero's own name appears in the ward's oldest "
            "logbook",
            "the recording, slowed down, is counting",
            "the nursery voice begins using the hero's name",
            "one photograph shows the hero, inside the window",
            "the ship asks a question back",
            "the guides fall silent when the hero enters — all but "
            "one",
            "the hero reads tomorrow's report and tries to change "
            "one word",
            "something knocks from behind the painted door, in the "
            "hero's rhythm",
        ),
        "climax": (
            "the hero must sit with the patient until the "
            "reflection returns — or doesn't",
            "the recording must be played aloud, once, in the "
            "quietest place",
            "the hero must answer the nursery voice, by name",
            "the hero must stand at the window's exact spot at the "
            "hour the photos change",
            "the hero must answer the ship's last question "
            "truthfully",
            "the hero follows the one still-playing guide to its "
            "exhibit",
            "the report is broadcast exactly as written, to see if "
            "it still happens",
            "the painted door must be opened from the hero's side, "
            "at night, alone",
        ),
        "resolution": (
            "the ward is quiet again, and the logbook gains a page "
            "nobody discusses",
            "the quietest place is found, and left off every map",
            "the children sleep; the house keeps one secret per "
            "room",
            "the photographs settle, except one, which is kept "
            "face-down",
            "the signal stops, mid-word, like a held breath let "
            "go",
            "the exhibition closes early, permanently, no reason "
            "given",
            "tomorrow's report is just weather, and it feels like "
            "mercy",
            "the staircase is walled up again — from both sides, "
            "this time",
        ),
    },
}

_OUTLINE_GENRES = tuple(_OUTLINE_BANKS)
_OUTLINE_DISPLAY = {"sci_fi": "sci-fi"}
_OUTLINE_SLOTS = ("protagonist", "inciting", "obstacle",
                  "midpoint", "climax", "resolution")


def _outline_parts(genre: str, rng: random.Random) -> dict:
    """Pure: one fragment per slot, drawn from the genre bank."""
    bank = _OUTLINE_BANKS[genre]
    return {slot: rng.choice(bank[slot]) for slot in _OUTLINE_SLOTS}


def story_outline(genre: str = "fantasy") -> str:
    """A three-act story outline for the chosen genre."""
    genre = (genre or "fantasy").strip().lower()
    if genre not in _OUTLINE_GENRES + ("any",):
        return (f"I don't have a '{genre}' bank — genres are "
                "fantasy, sci_fi, mystery, romance, horror or "
                "any (a surprise).")
    chosen = random.choice(_OUTLINE_GENRES) if genre == "any" \
        else genre
    parts = _outline_parts(chosen, random.Random())
    display = _OUTLINE_DISPLAY.get(chosen, chosen)
    return "\n".join([
        f"A {display} story outline:",
        f"Act 1 — Setup: {parts['protagonist']}. Everything "
        f"changes when {parts['inciting']}.",
        f"Act 2 — Conflict: {parts['obstacle']}. Midpoint "
        f"twist: {parts['midpoint']}.",
        f"Act 3 — Climax: {parts['climax']}. Resolution: "
        f"{parts['resolution']}.",
    ])
