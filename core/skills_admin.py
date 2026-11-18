"""
Admin skills pack for VYUK — the household-paperwork shelf:
a document vault index, a warranty log, an expiry radar over
both, contact cards, a service schedule with a due view, an
emergency card, a gift log, a borrow log and an asset
inventory.

Where each tool's truth lives — and what it honestly can't know:

- Everything persists as memory facts (core/memory/memory.py) — small
  JSON payloads under the fixed categories `document`,
  `warranty`, `contact`, `service`, `emergency`, `gift`,
  `borrow` and `asset` — so the records survive restarts and
  show up in the user's memory like anything else VYUK
  remembers. One line per fact: each payload is a single
  record, never a merged blob.
- The document vault is an INDEX, not a safe: it keeps a
  document's name, issuer and expiry date so renewals can be
  tracked — never the document itself and never its number.
  Any field that looks like a full ID number (12 or more
  digits in a row) is refused outright, and the save does not
  happen.
- expiry_radar and maintenance_due only read what the vault,
  warranty log and service schedule already hold. A date that
  can't be parsed is reported as unreadable, never guessed at,
  and an empty shelf gets an honest "nothing stored yet" line
  rather than a clean bill of health.
- Asset values are stored as plain numbers exactly as given:
  this pack never assumes a currency, and its sentences never
  print one.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; persistence sits behind
the module-level _store() seam so tests can substitute a fake;
stdlib only. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the registry
singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import datetime
import json
import re

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_admin_helpers import (
    _coerce_int,
    _coerce_float,
    _coerce_bool,
    _fmt_amount,
    _clean,
    _parse_date,
    _bad_date_line,
    _add_months,
    _days_words,
    _broken_note,
    _store,
    _facts,
    _save,
    _load,
    _nonempty,
    _MAX_NAME_CHARS,
    _MAX_TEXT_CHARS,
)
from .skills_admin_vault_logs import (
    _DOCUMENTS_CATEGORY,
    _ID_NUMBER_RE,
    _load_documents,
    document_vault,
    _WARRANTY_CATEGORY,
    _load_warranties,
    warranty_log,
    expiry_radar,
)
from .skills_admin_schedules import (
    _CONTACT_CATEGORY,
    _load_contacts,
    contact_card,
    _SERVICE_CATEGORY,
    _load_services,
    service_schedule,
    maintenance_due,
    _EMERGENCY_CATEGORY,
    _EMERGENCY_KEY,
    _EMERGENCY_FIELDS,
    emergency_card,
)
from .skills_admin_lend_assets import (
    _GIFT_CATEGORY,
    _load_gifts,
    gift_log,
    _BORROW_CATEGORY,
    _load_borrows,
    borrow_log,
    _ASSET_CATEGORY,
    _load_assets,
    asset_inventory,
    _HOME_CATEGORY,
    _load_home_items,
    home_inventory,
    warehouse_stock,
)
from .bookings import bookings
from .security_scan import security_scan
from .ip_camera import ip_camera
from .game_server import game_server
from .ebook_library import ebook_library
from .lodging import lodging
from .stage_lighting import stage_lighting
from .solar_yield import solar_yield
from .wardrobe import wardrobe
from .billing import billing
from .mcp_registry import mcp_registry
from .agent_call import agent_call
from .prompt_tune import prompt_tune
from .voice_agent import voice_agent


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Admin pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="document_vault",
        description="Keep an index of important documents — passport, driving licence, insurance papers, certificates — by name, issuer and expiry date, so renewals never sneak up. Actions: 'save' stores a document (name, issuer, expiry as YYYY-MM-DD, optional note), 'list' shows everything stored, soonest expiry first. PRIVACY: the vault stores names and dates ONLY — it refuses any field that looks like a full ID number (12 or more digits in a row). Records persist as memory facts. Use when the user mentions a document, a renewal or an expiry date, or asks what documents they have on file.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'save' or 'list'"},
                "name": {"type": "string", "description": "The document's name, e.g. 'Passport' — never its number"},
                "issuer": {"type": "string", "description": "Who issued it, e.g. 'Govt of India'"},
                "expiry": {"type": "string", "description": "The expiry date, as YYYY-MM-DD — blank if it has none"},
                "note": {"type": "string", "description": "Optional short note, e.g. 'renew at the RTO'"},
            },
            "required": ["action"],
        },
        handler=document_vault,
    )
    registry.register(
        name="warranty_log",
        description="Log purchases with their warranties and know when cover runs out. Actions: 'save' stores a product with the date it was bought and the warranty length in whole months — the cover end date is computed and stored with it — plus the shop if given; 'list' shows the log, soonest end first, each line saying whether cover is live or over. Records persist as memory facts. Use when the user buys something with a warranty, or asks what's still covered.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'save' or 'list'"},
                "product": {"type": "string", "description": "The product's name, for 'save'"},
                "bought_on": {"type": "string", "description": "The date it was bought, as YYYY-MM-DD"},
                "warranty_months": {"type": "integer", "description": "The warranty length in whole months, e.g. 12 or 24"},
                "shop": {"type": "string", "description": "Where it was bought, optional"},
            },
            "required": ["action"],
        },
        handler=warranty_log,
    )
    registry.register(
        name="expiry_radar",
        description="See every deadline the document vault and warranty log hold, in one read: what's already expired and what's expiring within the window (90 days by default), soonest first, each with its date and days remaining. Stored records whose dates can't be read are reported as unreadable, never guessed at. It only reads memory facts — nothing is sensed or looked up. Use when the user asks what's expiring, what's coming up for renewal, or wants a deadline check.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How far ahead to look, in days (default 90)"},
            },
            "required": [],
        },
        handler=expiry_radar,
    )
    registry.register(
        name="contact_card",
        description="Keep labelled contact details per person — the plumber's phone, a tutor's email, a landlord's address — exactly as labelled when saved. Actions: 'save' stores one detail for a person (label it in the detail itself, e.g. 'phone: +91 …'), 'show' reads back everything stored for that person. Records persist as memory facts. Use when the user dictates a number or address to keep, or asks for someone's details.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'save' or 'show'"},
                "person": {"type": "string", "description": "The person's name"},
                "detail": {"type": "string", "description": "The detail to store, labelled — e.g. 'phone: …', 'email: …', 'address: …'"},
            },
            "required": ["action"],
        },
        handler=contact_card,
    )
    registry.register(
        name="service_schedule",
        description="Track recurring upkeep — bike services, purifier filter changes, laptop deep-cleans — by when it was last done and how many months sit between services; the next due date is computed and stored. Actions: 'save' records an item (last done as YYYY-MM-DD, interval in whole months), 'list' shows the whole schedule, soonest due first, overdue items flagged. Records persist as memory facts. Use when the user gets something serviced or asks when something is next due.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'save' or 'list'"},
                "item": {"type": "string", "description": "What gets serviced, e.g. 'bike service'"},
                "last_done": {"type": "string", "description": "When it was last done, as YYYY-MM-DD"},
                "every_months": {"type": "integer", "description": "Months between services, e.g. 6 for twice a year"},
            },
            "required": ["action"],
        },
        handler=service_schedule,
    )
    registry.register(
        name="maintenance_due",
        description="The due view over the service schedule: what's overdue (with its due date and by how many days) and what's due within the next 30 days. It reads only what service_schedule stored — if nothing is scheduled, it says so instead of declaring everything fine. Use when the user asks what's due, what's overdue, or what upkeep is coming up.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=maintenance_due,
    )
    registry.register(
        name="emergency_card",
        description="Keep one consolidated emergency card: name, blood group, allergies, an emergency contact and a note, held as a single record. Actions: 'set' saves the card (any fields given), 'show' reads it back verbatim. It is stored locally in the app's memory on this machine only — never sent or synced anywhere. Use when the user shares emergency or medical details to keep, or asks what the card says.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'set' or 'show'"},
                "name": {"type": "string", "description": "The person's full name"},
                "blood_group": {"type": "string", "description": "Blood group, e.g. 'B+'"},
                "allergies": {"type": "string", "description": "Allergies, or 'none known'"},
                "emergency_contact": {"type": "string", "description": "Who to call in an emergency, with their number"},
                "note": {"type": "string", "description": "Anything else that matters, e.g. medication or conditions"},
            },
            "required": ["action"],
        },
        handler=emergency_card,
    )
    registry.register(
        name="gift_log",
        description="Keep gift ideas per person so a good idea is never lost — and never given twice. Actions: 'save' stores an idea for a person (occasion optional; set given to true once it's been handed over), 'list' shows the log grouped by person with ideas and already-given kept in separate lines. Records persist as memory facts. Use when the user has a gift idea, gives a gift, or asks what they were planning to get someone.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'save' or 'list'"},
                "person": {"type": "string", "description": "Who the gift is for"},
                "idea": {"type": "string", "description": "The gift idea itself"},
                "occasion": {"type": "string", "description": "The occasion, optional — e.g. 'birthday'"},
                "given": {"type": "boolean", "description": "True once the gift has actually been given (default false)"},
            },
            "required": ["action"],
        },
        handler=gift_log,
    )
    registry.register(
        name="borrow_log",
        description="Track things moving between people: what was lent out, what was borrowed in, who has it and since when. Actions: 'lend' records an item going out to a person, 'borrow' records an item coming in from a person (date optional for both — today if blank), 'returned' marks an item back, 'list' shows what's still out with the days counted. Records persist as memory facts. Use when the user lends or borrows anything, gets something back, or asks what's still out.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'lend', 'borrow', 'returned' or 'list'"},
                "item": {"type": "string", "description": "The item itself"},
                "person": {"type": "string", "description": "Who has it ('lend'), who it came from ('borrow'), or who had it ('returned', when several are out)"},
                "date": {"type": "string", "description": "The date of the loan, as YYYY-MM-DD — blank means today"},
            },
            "required": ["action"],
        },
        handler=borrow_log,
    )
    registry.register(
        name="asset_inventory",
        description="Keep an inventory of what's owned, with a value on each item and a total across the shelf. Actions: 'add' stores an item with an optional detail and a numeric value, 'list' shows every item with its value and the total. Values are plain numbers exactly as given — no currency is assumed. Records persist as memory facts. Use when the user lists possessions for insurance or their own records, or asks what their things add up to.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add' or 'list'"},
                "item": {"type": "string", "description": "The item's name, for 'add'"},
                "detail": {"type": "string", "description": "Optional detail — model, serial note, where it's kept"},
                "value": {"type": "number", "description": "The item's value as a plain number, no currency symbol"},
            },
            "required": ["action"],
        },
        handler=asset_inventory,
    )
    registry.register(
        name="home_inventory",
        description="Keep a home inventory: what an item is, which box and room hold it, its condition, value, purchase date and a photo, so nothing is lost in a move and an insurance claim can list it. Actions: 'add' stores an item with its box, room, condition, value, purchase date (YYYY-MM-DD), photo and quantity — one row per (item, box, room), adding the same item to the same box updates it; 'list' shows everything with its location and the total value; 'find' searches by item, box or room. The photo is a path to a file that already exists — the inventory records the path, it never copies or opens the file. Records persist as memory facts. Use when the user asks what's in a box, where something is stored, or what they own for an insurance claim.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'list' or 'find'"},
                "item": {"type": "string", "description": "The item's name — for 'add', and what to search for in 'find'"},
                "box": {"type": "string", "description": "The box or container holding the item, e.g. 'Box 3' — for 'add', and a box to search in 'find'"},
                "room": {"type": "string", "description": "The room holding the item or box, e.g. 'Garage' — for 'add', and a room to search in 'find'"},
                "condition": {"type": "string", "description": "The item's condition, e.g. 'new', 'good', 'worn'"},
                "value": {"type": "number", "description": "The item's value as a plain number, no currency symbol"},
                "purchased": {"type": "string", "description": "The purchase date, as YYYY-MM-DD"},
                "photo": {"type": "string", "description": "Path to a photo of the item — the file must already exist"},
                "detail": {"type": "string", "description": "Optional detail — model, serial note, contents"},
                "quantity": {"type": "integer", "description": "How many of the item are in the box — 1 if left out"},
            },
            "required": ["action"],
        },
        handler=home_inventory,
    )
    registry.register(
        name="warehouse_stock",
        description="Run a small warehouse or storage unit: stock locations with quantities, inbound (receive), outbound (ship), moves between locations, and per-location occupancy, so one operator can see what's where and how full each unit is. Actions: 'receive' adds stock to a location — capacity sets that location's capacity in units; 'ship' takes stock out and refuses when there is not enough on hand, never taking stock below zero; 'move' transfers stock between locations and refuses a shortfall at the source; 'list' shows stock by location with totals and recent movements; 'find' searches by item or location; 'occupancy' shows each location's fill against its capacity. One stock row per (item, location); every movement is written to a ledger. Records persist as memory facts. No billing, no staff roles, no customer portal, no hosted dashboard. Use when the user asks what's in a unit, where stock is, how full a unit is, or to receive, ship or move stock.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'receive', 'ship', 'move', 'list', 'find' or 'occupancy'"},
                "item": {"type": "string", "description": "The item's name — for 'receive', 'ship', 'move', and what to search for in 'find'"},
                "location": {"type": "string", "description": "The stock location, e.g. 'Aisle 3 · Bin 12' — where stock goes in 'receive', leaves in 'ship', and a location to filter in 'list' or search in 'find'"},
                "from_location": {"type": "string", "description": "The location the stock leaves, for 'move'"},
                "to_location": {"type": "string", "description": "The location the stock goes to, for 'move'"},
                "quantity": {"type": "integer", "description": "How many units — 1 if left out; a whole number, 1 or more"},
                "capacity": {"type": "integer", "description": "A location's capacity in units — set when receiving into a new location, or to change an existing location's capacity"},
                "detail": {"type": "string", "description": "Optional detail — model, serial note, contents"},
            },
            "required": ["action"],
        },
        handler=warehouse_stock,
    )
    registry.register(
        name="bookings",
        description="Take bookings for your own bookable resources — a lesson, a studio, a court, a rental: define the resource with its capacity, timezone, weekly schedule, buffers and booking window, then ask what's free, hold a slot, or book it, with the rules frozen at booking time. Actions: 'resource' defines a resource (capacity, timezone like 'Asia/Kolkata', schedule like 'Mon-Fri 09:00-17:00; Sat 10:00-14:00', buffer_minutes, booking_window_days, cancel_hours); 'availability' shows what's free and names what took each instant; 'hold' takes capacity temporarily and releases it when it expires; 'book' confirms a booking (two bookings can never take the same capacity — the store refuses a double booking; price is a recorded quote only, no payment is taken); 'cancel', 'reschedule' and 'no-show' apply the rules frozen when the booking was made; 'list' shows bookings; 'events' shows the hash-chained event ledger. Times are 'YYYY-MM-DD HH:MM' in the resource's timezone. No public booking page, no payment capture, no hosted dashboard. Use when the user asks what's free, to hold or book a slot, or to cancel, move or list bookings.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'resource', 'availability', 'hold', 'book', 'cancel', 'reschedule', 'no-show', 'list' or 'events'"},
                "resource": {"type": "string", "description": "The bookable resource's name, e.g. 'Studio A'"},
                "start": {"type": "string", "description": "Start as 'YYYY-MM-DD HH:MM' in the resource's timezone — for 'availability', 'hold', 'book', 'list' and 'reschedule'"},
                "end": {"type": "string", "description": "End as 'YYYY-MM-DD HH:MM' in the resource's timezone"},
                "customer": {"type": "string", "description": "Who the hold or booking is for"},
                "capacity": {"type": "integer", "description": "How many bookings can overlap on the resource — set when defining it, 1 or more"},
                "timezone": {"type": "string", "description": "The resource's IANA timezone, e.g. 'Asia/Kolkata' or 'Europe/Rome'"},
                "schedule": {"type": "string", "description": "Weekly windows like 'Mon-Fri 09:00-17:00; Sat 10:00-14:00', in the resource's local time"},
                "buffer_minutes": {"type": "integer", "description": "Minutes kept free after each booking — 0 if left out"},
                "booking_window_days": {"type": "integer", "description": "How far ahead bookings may be made, in days — 30 if left out"},
                "cancel_hours": {"type": "integer", "description": "Free cancellation until this many hours before the start — 24 if left out"},
                "hold_minutes": {"type": "integer", "description": "How long a hold lasts before it releases itself, in minutes — 30 if left out"},
                "price": {"type": "number", "description": "The booking's price as a recorded quote — no payment is taken"},
                "idempotency_key": {"type": "string", "description": "A caller key for the booking — booking twice under the same key returns the first booking instead of booking twice"},
                "booking_id": {"type": "string", "description": "The booking to cancel, reschedule, mark no-show, or show events for — e.g. 'bk_...'"},
                "detail": {"type": "string", "description": "Optional note on the resource or booking"},
            },
            "required": ["action"],
        },
        handler=bookings,
    )
    registry.register(
        name="security_scan",
        description="Scan installed software for known vulnerabilities, secrets and licences, and generate a package inventory. Actions: 'vuln' checks installed Python packages against the OSV vulnerability feed (a keyless public feed; package + version checks one package, or path=<a requirements.txt> checks its pins; a fetch failure is reported honestly, never as a clean result); 'secrets' scans a file or folder for leaked secrets (masked findings only, nothing leaves the machine); 'licenses' checks installed packages' licences against the allow-list; 'sbom' returns a CycloneDX inventory of installed packages. Misconfiguration, container-image and Kubernetes scanning are not built: VYUK runs no containers, no Kubernetes and no IaC. Use when the user asks whether their installed software has known vulnerabilities, whether a folder leaks secrets, whether their packages' licences are allowed, or for a list of everything installed.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'vuln', 'secrets', 'licenses' or 'sbom'"},
                "package": {"type": "string", "description": "One package to check with 'vuln', e.g. 'requests' — leave out to check all installed packages"},
                "version": {"type": "string", "description": "The version to check with 'vuln' — the installed version if left out and the package is installed"},
                "path": {"type": "string", "description": "With 'vuln', a requirements.txt whose == pins to check; with 'secrets', the file or folder to scan"},
                "limit": {"type": "integer", "description": "Most findings shown per package (vuln, 10 if left out) or overall (secrets, 50 if left out)"},
            },
            "required": ["action"],
        },
        handler=security_scan,
    )
    registry.register(
        name="ip_camera",
        description="Use the owner's own IP cameras (RTSP/ONVIF/MJPEG) on the local network — no cloud, no vendor app. Actions: 'discover' finds ONVIF cameras on the LAN; 'add' saves one (name + rtsp:// URL, optional snapshot URL); 'snapshot' saves one JPEG frame from the camera's HTTP snapshot URL; 'mjpeg' reads a bounded MJPEG stream and saves the last frame; 'rtsp' probes the RTSP stream and reports the codec, resolution and frame rate (packet-level only — frames are never decoded, so there is no live view); 'record' saves a bounded raw .h264 recording (never an .mp4, which needs an encoder VYUK does not ship); 'list' shows saved cameras; 'remove' forgets one. Camera actions need the camera permission for the session; a refused camera is an honest refusal. WebRTC restream and HLS playback are NOT built. Use when the user asks about an IP camera, a CCTV or security camera, RTSP or ONVIF.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'discover', 'add', 'snapshot', 'mjpeg', 'rtsp', 'record', 'list' or 'remove'"},
                "camera": {"type": "string", "description": "The camera's saved name — for 'add', the name to save it under"},
                "url": {"type": "string", "description": "With 'add', the camera's rtsp:// URL"},
                "snapshot_url": {"type": "string", "description": "With 'add', the camera's http(s):// snapshot URL, if it has one"},
                "kind": {"type": "string", "description": "With 'add', a label for the camera, e.g. 'front door'"},
                "username": {"type": "string", "description": "Camera login name, for cameras that need one — given per call, never stored"},
                "password": {"type": "string", "description": "Camera password, for cameras that need one — given per call, never stored, never echoed back"},
                "duration_seconds": {"type": "integer", "description": "How long to read the feed for 'mjpeg', 'rtsp' or 'record', in seconds (1 to 30; 5 if left out)"},
                "path": {"type": "string", "description": "Where to save the JPEG or .h264 file — a default Pictures/Videos location if left out"},
                "limit": {"type": "integer", "description": "With 'discover', most cameras to show (10 if left out)"},
                "detail": {"type": "string", "description": "Optional note, unused by the camera actions"},
            },
            "required": ["action"],
        },
        handler=ip_camera,
    )
    registry.register(
        name="game_server",
        description="Run, watch and talk to the owner's own game servers (Minecraft / Steam / generic) on this machine — no cloud, no hosting panel. Actions: 'add' saves a server the owner already has (name + kind + the command that starts it + where it lives + query/RCON ports); 'remove' forgets one; 'list' shows saved servers; 'start' launches one; 'stop'/'restart' stop one; 'status' reports whether its process is running under VYUK plus a best-effort query; 'query' asks the server itself (Minecraft Server List Ping, or Steam A2S); 'console' reads the tail of its captured output; 'send' writes one line to its console stdin; 'rcon' runs one command over RCON (password per call, never stored or echoed). Starting/stopping/sending/RCON need the human's confirmation. The server binary is operator-supplied — VYUK never downloads, installs or ships one. One-click deployment, Docker, multi-user and hosting-panel shapes are NOT built. Use when the user asks about a Minecraft server, a Steam game server, RCON, or starting/stopping a game server.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'remove', 'list', 'start', 'stop', 'restart', 'status', 'query', 'console', 'send' or 'rcon'"},
                "server": {"type": "string", "description": "The server's saved name — for 'add', the name to save it under"},
                "kind": {"type": "string", "description": "With 'add': 'minecraft', 'steam' or 'generic'"},
                "command": {"type": "string", "description": "With 'add', the command that starts the server you already have; with 'send'/'rcon', the console line / RCON command to run"},
                "working_dir": {"type": "string", "description": "With 'add', the folder the server runs in"},
                "host": {"type": "string", "description": "With 'add', the host to query (127.0.0.1 if left out)"},
                "port": {"type": "integer", "description": "With 'add', the query port (25565 Minecraft, 27015 Steam if left out)"},
                "rcon_host": {"type": "string", "description": "With 'add'/'rcon', the RCON host (the query host if left out)"},
                "rcon_port": {"type": "integer", "description": "With 'add'/'rcon', the RCON port (25575 Minecraft if left out)"},
                "rcon_password": {"type": "string", "description": "With 'rcon', the RCON password — given per call, never stored, never echoed back"},
                "lines": {"type": "integer", "description": "With 'console', most lines to show (50 if left out)"},
                "timeout_seconds": {"type": "integer", "description": "How long to wait for a query or RCON reply, in seconds (1 to 30; 5 if left out)"},
            },
            "required": ["action"],
        },
        handler=game_server,
    )
    registry.register(
        name="ebook_library",
        description="Keep the owner's own ebooks and comics (EPUB/CBZ) in a library, track reading progress, and serve them to their readers over the local network — no cloud, no hosted server. Actions: 'scan' reads a folder into the library (EPUB + CBZ only; CBR and PDF are counted as skipped, not claimed); 'list' shows the books with their progress; 'search' finds by title or author; 'progress' shows or sets one book's progress (percentage 0-100, page, or a KOReader progress string); 'serve' starts the LAN server — an OPDS 1.2 catalogue plus KOReader Sync — and prints its address and password; 'stop' stops it; 'status' reports the server and the library; 'remove' forgets one book (the file is never deleted). The server binds the LAN address only, needs a fresh password per start, and serves only registered books. Kobo Sync and OPDS v2 are NOT built. Use when the user asks about ebooks, comics, an ebook library, OPDS, KOReader sync, or reading on their e-reader.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'scan', 'list', 'search', 'progress', 'serve', 'stop', 'status' or 'remove'"},
                "path": {"type": "string", "description": "With 'scan', the folder to scan for EPUB/CBZ books"},
                "book": {"type": "string", "description": "With 'search'/'progress'/'remove', the book's title (or its hash)"},
                "author": {"type": "string", "description": "With 'search', an author to search for"},
                "percentage": {"type": "number", "description": "With 'progress', the percentage read, 0-100"},
                "page": {"type": "integer", "description": "With 'progress', the page reached"},
                "progress": {"type": "string", "description": "With 'progress', a KOReader progress string (a page number, or a /body/DocFragment[N].0 xpath for EPUB)"},
                "device": {"type": "string", "description": "With 'progress', the device reporting the progress"},
                "device_id": {"type": "string", "description": "With 'progress', the reporting device's id"},
                "limit": {"type": "integer", "description": "Most books shown by 'list'/'search' (50 if left out)"},
                "port": {"type": "integer", "description": "With 'serve', the port to listen on (a free one is picked if left out)"},
            },
            "required": ["action"],
        },
        handler=ebook_library,
    )
    registry.register(
        name="lodging",
        description="Run the owner's own small lodging — a cabin, a campsite, a lodge, a yurt — as the supplier: properties, units, seasonal rates, nightly availability with a quote, stays, guests, housekeeping and the day view. Actions: 'property' adds or shows a property (timezone, currency, tax, turnover nights, deposit and cancellation policy); 'unit' adds or shows a room/site (type, sleeps, nightly rate, minimum stay); 'rate' sets or lists a seasonal rate for a unit; 'availability' shows whether a unit (or every unit of a property) is free for a date range and quotes it; 'book' books a stay (the double-booking guarantee is a SQLite trigger: an overlapping stay in a blocking status aborts the write, turnover nights included); 'cancel' cancels a reserved stay and states the refund under the frozen cancellation policy; 'checkin' checks a reserved stay in (the unit must be housekeeping-ready); 'checkout' checks a stay out and marks the unit dirty; 'no-show' marks a reserved stay a no-show; 'guest' shows a guest's stays; 'housekeeping' shows or moves a unit's housekeeping state (ready/dirty/cleaning/inspection/do_not_disturb/out_of_service, along allowed transitions only); 'day' shows one property's arrivals, departures, in-house, occupancy and expected take; 'list' lists stays; 'events' shows the hash-chained event ledger. Money is integer cents; tax is one rounding on the aggregate nightly base; a stay is [check_in, check_out); no money moves — the deposit and balance are amounts due, there is no payment capture, no public booking page, no hosted anything and no multi-user accounts. The booking-engine half is 'bookings' — it is NOT rebuilt here. Use when the user asks about their lodging, cabins, campsites, rooms, rates, stays, guests, check-in/out or housekeeping.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'property', 'unit', 'rate', 'availability', 'book', 'cancel', 'checkin', 'checkout', 'no-show', 'guest', 'housekeeping', 'day', 'list' or 'events'"},
                "property": {"type": "string", "description": "With 'property'/'unit'/'rate'/'availability'/'day'/'list', the property's name"},
                "unit": {"type": "string", "description": "With 'unit'/'rate'/'availability'/'book'/'housekeeping'/'list', the unit's name"},
                "guest": {"type": "string", "description": "With 'book'/'guest', the guest's name"},
                "contact": {"type": "string", "description": "With 'book'/'guest', the guest's contact (phone or email)"},
                "check_in": {"type": "string", "description": "With 'rate'/'availability'/'book', the first night, YYYY-MM-DD"},
                "check_out": {"type": "string", "description": "With 'rate'/'availability'/'book', the morning after the last night, YYYY-MM-DD"},
                "stay_id": {"type": "integer", "description": "With 'cancel'/'checkin'/'checkout'/'no-show', the stay number"},
                "date": {"type": "string", "description": "With 'day'/'list', the date, YYYY-MM-DD (the property-local today if left out)"},
                "state": {"type": "string", "description": "With 'housekeeping', the target housekeeping state; with 'list', a stay status to filter by"},
                "cents": {"type": "integer", "description": "With 'unit', the default nightly rate in cents; with 'rate', the seasonal nightly rate in cents"},
                "min_nights": {"type": "integer", "description": "With 'unit'/'rate', the minimum stay in nights"},
                "sleeps": {"type": "integer", "description": "With 'unit', how many guests the unit sleeps"},
                "type": {"type": "string", "description": "With 'unit', the unit type, e.g. 'cabin', 'site', 'room', 'yurt'"},
                "timezone": {"type": "string", "description": "With 'property', the property's IANA timezone"},
                "currency": {"type": "string", "description": "With 'property', the currency label, e.g. 'USD', 'CAD', 'EUR'"},
                "tax_rate_bps": {"type": "integer", "description": "With 'property', the tax rate in basis points (1300 = 13%)"},
                "turnover_nights": {"type": "integer", "description": "With 'property', the turnover nights held after each checkout"},
                "deposit": {"type": "string", "description": "With 'property', the deposit policy: 'full', 'percent:50', 'first_night' or 'flat:20000'"},
                "cancel_policy": {"type": "string", "description": "With 'property', the cancellation policy: 'days:percent' pairs, e.g. '14:100;7:50;0:0'"},
                "limit": {"type": "integer", "description": "Most stays or events shown by 'list'/'events' (20 if left out)"},
                "detail": {"type": "integer", "description": "With 'list', 1 to include the frozen folio breakdown"},
            },
            "required": ["action"],
        },
        handler=lodging,
    )
    registry.register(
        name="stage_lighting",
        description="Run the owner's own stage / venue lighting as the supplier: Art-Net and sACN (E1.31) DMX output to the buyer's own lighting nodes on the LAN, fixture patching, scenes and chases. Actions: 'node' adds, lists or removes a lighting node (protocol, host, universe — a host left out means Art-Net broadcast / sACN multicast for that universe); 'fixture' defines a fixture's channel map ('dimmer:1;red:2;green:3;blue:4'); 'patch' patches a fixture to a universe + start address (a SQLite trigger refuses overlapping patches on the same universe); 'scene' saves, lists, runs or deletes a scene (a saved set of channel levels for one universe); 'chase' saves, lists, steps or deletes a chase (an ordered list of scenes with hold times, stepped by the operator); 'output' sends channel levels to a universe through its node; 'blackout' zeroes all 512 channels of a universe; 'discover' finds Art-Net nodes on the LAN (OpPoll — sACN has no discovery, add sACN nodes by address); 'status' shows nodes, patches, scenes, chases and the last send; 'events' shows the hash-chained event ledger. Sending (output, blackout, scene run, chase step) is human-gated; registry writes and reads stay free. DMX over USB, the virtual console / web UI / hosted shape, QLC+ fixture-library import and timed auto-advance of a chase are NOT built. Use when the user asks about their stage lighting, DMX, Art-Net, sACN, fixtures, patches, scenes or chases.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'node', 'patch', 'fixture', 'scene', 'chase', 'output', 'blackout', 'discover', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'node'/'patch'/'fixture': 'add', 'list' or 'remove'; with 'scene': 'save', 'list', 'run' or 'delete'; with 'chase': 'save', 'list', 'step' or 'delete' (the default is 'add'/'save' when the defining properties are given, else 'list')"},
                "node": {"type": "string", "description": "With 'node', the node's name; with 'output'/'blackout', the node to send through (the first node for the universe if left out); with 'status', a node to show"},
                "host": {"type": "string", "description": "With 'node', the node's IP or hostname (left out means Art-Net broadcast / sACN multicast for the universe); with 'discover', the address to poll (broadcast if left out)"},
                "protocol": {"type": "string", "description": "With 'node', 'artnet' or 'sacn'"},
                "universe": {"type": "integer", "description": "With 'node'/'patch'/'scene'/'output'/'blackout', the DMX universe, 0-63999 (Art-Net nodes take 0-32767, sACN nodes 1-63999)"},
                "address": {"type": "integer", "description": "With 'patch', the DMX start address, 1-512"},
                "fixture": {"type": "string", "description": "With 'fixture'/'patch', the fixture definition's name"},
                "type": {"type": "string", "description": "With 'fixture', the fixture type, e.g. 'dimmer', 'rgb' or 'moving_head'"},
                "channels": {"type": "string", "description": "With 'fixture', the channel map, 'name:offset;name:offset', e.g. 'dimmer:1;red:2;green:3;blue:4'"},
                "scene": {"type": "string", "description": "With 'scene', the scene's name"},
                "chase": {"type": "string", "description": "With 'chase', the chase's name"},
                "steps": {"type": "string", "description": "With 'chase', the steps, 'scene:hold_ms;scene:hold_ms', e.g. 'warm:2000;cool:2000'"},
                "levels": {"type": "string", "description": "With 'scene'/'output', the channel levels, 'channel:value;channel:value', e.g. '1:255;2:128'"},
                "limit": {"type": "integer", "description": "Most events shown by 'events' (20 if left out)"},
            },
            "required": ["action"],
        },
        handler=stage_lighting,
    )
    registry.register(
        name="solar_yield",
        description="Model the buyer's own solar PV array from scratch (stdlib math — sun position, clear-sky irradiance, module/inverter yield) for sizing and quoting real arrays. Actions: 'sun' gives the sun's position at a date and time (apparent zenith/azimuth, declination, hour angle, equation of time, airmass, extraterrestrial irradiance, solar noon — no sunrise/sunset times, the NOAA sunrise_sunset tool owns those); 'irradiance' gives clear-sky GHI/DNI/DHI plus plane-of-array irradiance (beam, sky, ground) for a tilted surface; 'yield' gives the clear-sky energy of an array over a date or a date range (hourly integration over representative mid-month days, PVWatts DC + inverter, Ross cell temperature). Pure computation over caller-supplied figures — read-only, nothing stored, nothing fetched. All times UTC; latitude/longitude in decimal degrees; tilt from horizontal and azimuth clockwise from north (180 = south-facing in the northern hemisphere). Clear-sky estimates only: no clouds, shading, soiling, snow, mismatch or wiring losses. Use when the user asks about solar position, sun angle, irradiance, PV yield, panel output or sizing a solar array.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'sun', 'irradiance' or 'yield'"},
                "latitude": {"type": "number", "description": "Site latitude in decimal degrees, -90 to 90"},
                "longitude": {"type": "number", "description": "Site longitude in decimal degrees, -180 to 180"},
                "date": {"type": "string", "description": "With 'sun'/'irradiance', the date, yyyy-mm-dd (today if left out); with 'yield', the single date or the range start"},
                "time": {"type": "string", "description": "With 'sun'/'irradiance', the time in UTC, HH:MM (12:00 if left out)"},
                "tilt": {"type": "number", "description": "Panel tilt from horizontal in degrees, 0-90 (30 if left out)"},
                "azimuth": {"type": "number", "description": "Panel azimuth clockwise from north in degrees, 0-360 (180 if left out — south-facing in the northern hemisphere)"},
                "altitude": {"type": "number", "description": "Site altitude in metres, 0-9000 (0 if left out)"},
                "linke_turbidity": {"type": "number", "description": "Linke turbidity for the clear-sky model, 1-10 (3.0 if left out)"},
                "albedo": {"type": "number", "description": "Ground albedo, 0-1 (0.2 if left out)"},
                "temp_air": {"type": "number", "description": "Air temperature in °C, -40 to 60 (25 if left out)"},
                "pdc0": {"type": "number", "description": "With 'yield', the array's DC rating in watts (5000 if left out)"},
                "gamma_pdc": {"type": "number", "description": "With 'yield', the module power temperature coefficient per °C, -0.02 to 0 (-0.004 if left out)"},
                "noct": {"type": "number", "description": "With 'yield', the module nominal operating cell temperature in °C, 20-70 (45 if left out)"},
                "inverter_ac0": {"type": "number", "description": "With 'yield', the inverter's AC rating in watts (the DC rating if left out)"},
                "eta_inv_nom": {"type": "number", "description": "With 'yield', the inverter's nominal efficiency, 0.5-1 (0.96 if left out)"},
                "start_date": {"type": "string", "description": "With 'yield', the range start, yyyy-mm-dd (the date if left out)"},
                "end_date": {"type": "string", "description": "With 'yield', the range end, yyyy-mm-dd (the start if left out); at most 366 days"},
                "detail": {"type": "integer", "description": "With 'yield', 1 to include the hourly AC power profile of each representative day"},
            },
            "required": ["action"],
        },
        handler=solar_yield,
    )
    registry.register(
        name="wardrobe",
        description="Keep the owner's own wardrobe — garments, outfits, daily suggestions, schedule and wear history. Actions: 'garment' adds/lists/removes a garment (name, category top/bottom/dress/outerwear/shoes/accessory, colour, seasons, warmth 1-5, formality 1-5, photo path to an existing file — recorded, never copied or opened); 'outfit' builds/lists/removes an outfit from owned garments (a second garment in the same single slot — top, bottom, dress, shoes — is refused, as is a dress with a top or bottom; an incomplete outfit builds with a warning about the missing slot); 'suggest' picks an outfit for a date from the day's temperature and condition plus wear history (least-recently-worn first, saved outfits not worn in the last 7 days preferred, warmth matched to the temperature); 'schedule' plans an outfit for a date (one outfit per date); 'wear' marks an outfit worn on a date (updates each garment's last-worn); 'status' counts the wardrobe; 'events' shows the hash-chained event ledger. No AI outfit visualisation, no garment extraction from photos, no camera-roll scan, no weather fetch, no sharing, no hosted anything. Use when the user asks about their wardrobe, clothes, garments, outfits, what to wear or planning what to wear.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'garment', 'outfit', 'suggest', 'schedule', 'wear', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'garment'/'outfit'/'schedule': 'add', 'build', 'list' or 'remove'"},
                "name": {"type": "string", "description": "With 'garment' add / 'outfit' build, the garment or outfit name"},
                "category": {"type": "string", "description": "With 'garment' add/list, the garment category: 'top', 'bottom', 'dress', 'outerwear', 'shoes' or 'accessory'"},
                "colour": {"type": "string", "description": "With 'garment' add, the garment colour, e.g. 'navy'"},
                "seasons": {"type": "string", "description": "With 'garment' add, the seasons it suits, e.g. 'summer;autumn' (empty means all seasons)"},
                "warmth": {"type": "integer", "description": "With 'garment' add, warmth 1 (light) to 5 (heavy); 3 if left out"},
                "formality": {"type": "integer", "description": "With 'garment' add, formality 1 (casual) to 5 (formal); 3 if left out; with 'suggest', the minimum formality wanted"},
                "photo": {"type": "string", "description": "With 'garment' add, the path to an existing photo file of the garment (recorded, never copied or opened)"},
                "garment_id": {"type": "integer", "description": "With 'garment' remove, the garment number"},
                "garment_ids": {"type": "string", "description": "With 'outfit' build, the garment numbers, e.g. '1;2;3'"},
                "outfit_id": {"type": "integer", "description": "With 'outfit' remove / 'schedule' add / 'wear', the outfit number"},
                "schedule_id": {"type": "integer", "description": "With 'schedule' remove, the scheduled entry number"},
                "date": {"type": "string", "description": "With 'garment' list, a date whose season filters the list; with 'suggest'/'schedule'/'wear', the date, YYYY-MM-DD (today if left out for 'suggest'/'wear')"},
                "temp_c": {"type": "integer", "description": "With 'suggest', today's temperature in Celsius (20 if left out)"},
                "condition": {"type": "string", "description": "With 'suggest', today's condition, e.g. 'rain' or 'clear'"},
                "note": {"type": "string", "description": "With 'outfit' build / 'schedule' add, a note on the outfit or the scheduled day"},
                "limit": {"type": "integer", "description": "Most garments, outfits, scheduled entries or events shown (20 if left out)"},
                "detail": {"type": "integer", "description": "With 'garment'/'outfit' list, 1 to include photo paths and notes"},
            },
            "required": ["action"],
        },
        handler=wardrobe,
    )
    registry.register(
        name="billing",
        description="Test-mode-only payment plumbing over the Stripe API (own stdlib client, no SDK). Actions: 'customer' creates/lists/gets a TEST customer (name, email); 'subscription' creates a TEST subscription from an existing Price ID, cancels one, or reports its status; 'payment_link' creates a TEST payment link from an existing Price ID or gets one; 'checkout' creates a TEST Checkout session (checkout_mode 'payment' or 'subscription', success_url + cancel_url supplied by the caller); 'invoice' lists/gets TEST invoices; 'refund' creates a TEST refund for a payment intent or charge (amount in whole minor units, reason duplicate/fraudulent/requested_by_customer, approval_ticket — the out-of-band approval reference, required by MN-5.14 rule 7); 'webhook' verifies a Stripe-Signature header against a pasted payload and endpoint secret (pure local HMAC-SHA256, no server); 'status' counts the local test-mode ledger; 'events' shows the hash-chained ledger. TEST MODE ONLY: only keys starting 'sk_test_' are accepted, live keys are refused outright, every answer is checked for livemode false, no prices/tiers/trials are ever set (Price IDs are operator-supplied), no live money moves, no card is ever handled. The test key and webhook secret are operator-supplied per call only, never stored, never echoed. Creating a test customer/subscription/payment link/Checkout session/refund and cancelling a test subscription ask first; reads do not. Use when the user asks about testing their Stripe billing, test customers, test subscriptions, test payment links, test checkout, test invoices, test refunds, or verifying a Stripe webhook signature.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'customer', 'subscription', 'payment_link', 'checkout', 'invoice', 'refund', 'webhook', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'customer': 'create', 'list' or 'get'; 'subscription': 'create', 'cancel' or 'status'; 'payment_link': 'create' or 'get'; 'checkout'/'refund': 'create'; 'invoice': 'list' or 'get'; 'webhook': 'verify'"},
                "customer_id": {"type": "string", "description": "With 'customer' get / 'subscription' create / 'invoice' list, the Stripe customer id (cus_...)"},
                "name": {"type": "string", "description": "With 'customer' create, the customer's name"},
                "email": {"type": "string", "description": "With 'customer' create, the customer's email"},
                "price_id": {"type": "string", "description": "With 'subscription'/'payment_link'/'checkout' create, an existing Stripe Price ID (price_...), created by the operator in their own Stripe dashboard — VYUK never creates a price"},
                "quantity": {"type": "integer", "description": "With 'subscription'/'payment_link'/'checkout' create, the quantity, a whole number 1 or more (1 if left out)"},
                "amount": {"type": "integer", "description": "With 'refund' create, the amount to refund in whole minor units (cents/paise, never floats); whole refund if left out"},
                "subscription_id": {"type": "string", "description": "With 'subscription' cancel/status, the subscription id (sub_...)"},
                "payment_link_id": {"type": "string", "description": "With 'payment_link' get, the payment link id (plink_...)"},
                "invoice_id": {"type": "string", "description": "With 'invoice' get, the invoice id (in_...)"},
                "payment_intent_id": {"type": "string", "description": "With 'refund' create, the payment intent id (pi_...)"},
                "charge_id": {"type": "string", "description": "With 'refund' create, the charge id (ch_...) — use instead of payment_intent_id"},
                "reason": {"type": "string", "description": "With 'refund' create, 'duplicate', 'fraudulent' or 'requested_by_customer'"},
                "approval_ticket": {"type": "string", "description": "With 'refund' create, the out-of-band approval reference for the refund (MN-5.14 rule 7: VYUK never issues a refund on a chat instruction alone) — required, supplied per call, never stored"},
                "payload": {"type": "string", "description": "With 'webhook' verify, the raw payload Stripe sent, exactly as received"},
                "signature": {"type": "string", "description": "With 'webhook' verify, the Stripe-Signature header value (t=...,v1=...)"},
                "webhook_secret": {"type": "string", "description": "With 'webhook' verify, the endpoint secret (whsec_...), operator-supplied per call only, never stored or echoed"},
                "api_key": {"type": "string", "description": "The operator's Stripe TEST secret key (sk_test_...), supplied per call only, never stored or echoed; live keys are refused"},
                "success_url": {"type": "string", "description": "With 'checkout' create, the URL Stripe sends the buyer to after a successful test payment"},
                "cancel_url": {"type": "string", "description": "With 'checkout' create, the URL Stripe sends the buyer to if they abandon the test checkout"},
                "checkout_mode": {"type": "string", "description": "With 'checkout' create, 'payment' (a one-off test payment) or 'subscription' (a test subscription)"},
                "limit": {"type": "integer", "description": "With 'events', most ledger entries shown (20 if left out)"},
                "detail": {"type": "integer", "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=billing,
    )
    registry.register(
        name="mcp_registry",
        description="Local single-user MCP server registry over VYUK's own MCP client (the registry half of IBM's ContextForge, rebuilt from scratch — no code adopted). Actions: 'server' manages the server directory — mode 'add' registers a server ('command' + 'args' + 'env' for stdio, or 'url' + 'headers' + 'auth' for remote; auth secrets arrive as environment-variable NAMES, never literals), 'list' shows every server with transport, enabled state and live status, 'remove' unregisters one (a built-in default cannot be removed — disable it instead), 'enable'/'disable' switch one on or off (takes effect immediately, no restart); 'discover' is central discovery — every server and every tool it exposes, with each tool's operator-review class and whether its description changed since it was last seen ('connect' true first asks the bus to connect every enabled server, bounded); 'review' records an operator review for one tool ('klass' 'read', 'egress' or 'confirm', 'reviewer'; the tool must have been seen, so the review is bound to its current declaration digest); 'policy' lists the review store with each record's current validity; 'status' counts servers, tools, reviews and ledger events; 'events' shows the hash-chained ledger of the trust decisions. No hosted gateway, no MCP server endpoint, no A2A/REST/gRPC federation, and it never executes an MCP tool — calls still flow through 'use_toolbox' into the funnel's MCP branch. Adding, removing, enabling, disabling a server and recording a review ask first; reads do not. Use when the user asks about managing their MCP servers, discovering MCP tools, or reviewing MCP tools.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'server', 'discover', 'review', 'policy', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'server': 'add', 'list', 'remove', 'enable' or 'disable'"},
                "server": {"type": "string", "description": "With 'server' add/remove/enable/disable, the server's name"},
                "command": {"type": "string", "description": "With 'server' add (stdio), the program to run, e.g. 'mcp-server-fetch'"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "With 'server' add (stdio), the program's arguments, a list of strings"},
                "url": {"type": "string", "description": "With 'server' add (remote), the server's URL"},
                "env": {"type": "object", "description": "With 'server' add (stdio), environment variables for the program, an object of name to value"},
                "headers": {"type": "object", "description": "With 'server' add (remote), HTTP headers, an object of name to value"},
                "auth": {"type": "object", "description": "With 'server' add (remote), the auth block — type 'client_credentials' with issuer, client_id and client_secret_env (the NAME of an environment variable, never the secret itself)"},
                "note": {"type": "string", "description": "With 'server' add, a note on the server"},
                "tool": {"type": "string", "description": "With 'review', the composed tool name, e.g. 'mcp__fetch__fetch'"},
                "klass": {"type": "string", "description": "With 'review', 'read', 'egress' or 'confirm'"},
                "reviewer": {"type": "string", "description": "With 'review', who reviewed it ('operator' if left out)"},
                "connect": {"type": "boolean", "description": "With 'discover', true to connect every enabled server first (bounded)"},
                "limit": {"type": "integer", "description": "With 'discover'/'events', most tools or ledger entries shown (50 for discover, 20 for events, if left out)"},
                "detail": {"type": "integer", "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=mcp_registry,
    )
    registry.register(
        name="agent_call",
        description="A2A client — VYUK calls another agent, never serves as one (the client half of a2aproject/A2A, rebuilt from scratch — no code adopted). Actions: 'discover' fetches an agent's public card (GET /.well-known/agent-card.json on an operator-supplied URL) and records it in the local single-user registry — the card must declare a JSON-RPC interface and no security requirements (authenticated agents are refused: VYUK stores no credentials); 'list' shows every known agent; 'call' sends a message to a known agent (JSON-RPC 2.0 SendMessage over HTTPS; plain HTTP only on loopback) and reports the task state plus the artifacts / agent message it returns; 'task' with mode 'get'/'list' reads a task or lists tasks, mode 'cancel' cancels one; 'remove' forgets a known agent; 'card' returns VYUK's own agent card as a static document (supportedInterfaces empty — VYUK serves no endpoint and accepts no inbound calls); 'status' counts known agents and ledger events; 'events' shows the hash-chained ledger of what VYUK sent to whom. No streaming (SSE), no push-notification configs, no extended agent card, no gRPC binding, no multi-user or shared directory. Calling, cancelling or forgetting asks first; reads do not. Use when the user asks about calling an external agent, discovering an agent, or checking what an agent answered.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'discover', 'list', 'call', 'task', 'remove', 'card', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'task': 'get', 'list' or 'cancel'"},
                "url": {"type": "string", "description": "With 'discover', the agent's URL (its base URL or its full /.well-known/agent-card.json URL), operator-supplied per call"},
                "agent": {"type": "string", "description": "With 'call', 'task' or 'remove', the name of a discovered agent"},
                "text": {"type": "string", "description": "With 'call', the message to send to the agent"},
                "task_id": {"type": "string", "description": "With 'call', the task to continue; with 'task' get/cancel, the task to read or cancel"},
                "context_id": {"type": "string", "description": "With 'call'/'task' list, the conversation context to continue or filter by"},
                "history_length": {"type": "integer", "description": "With 'call', most recent task messages to ask the agent to include in its answer"},
                "return_immediately": {"type": "boolean", "description": "With 'call', true to return as soon as the task is created instead of waiting for a terminal state"},
                "status": {"type": "string", "description": "With 'task' list, the task state to filter by, e.g. 'TASK_STATE_WORKING'"},
                "page_size": {"type": "integer", "description": "With 'task' list, most tasks to ask for (1-100)"},
                "limit": {"type": "integer", "description": "With 'events', most ledger entries shown (20 if left out)"},
                "detail": {"type": "integer", "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=agent_call,
    )
    registry.register(
        name="prompt_tune",
        description="Local prompt optimisation + evaluation — the optimisation half of DSPy, rebuilt from scratch (no code adopted). Actions: 'prompt' keeps prompts (add/list/get/remove/set_current); 'dataset' keeps evaluation sets the owner supplies (input/expected pairs, JSON, local only); 'evaluate' runs one prompt against one dataset through the owner's own configured Gemini endpoint and reports per-example scores plus the mean (metric 'exact', 'contains' or 'f1'); 'tune' ranks candidates — operator-supplied prompts, the built-in variant bank applied to a base prompt, and/or LM-proposed rewrites — on the same dataset and promotes the winner to current; 'variants' lists the built-in variant bank; 'propose' asks the LM for rewrites of a base prompt; 'status' counts prompts, datasets and runs and reports whether the LM is reachable; 'events' shows the hash-chained ledger. No DSPy framework port, no weight training, no cloud optimisation, no LLM-as-judge scoring (scores are deterministic). The API key is the product's own configured key — never passed as an argument, never echoed, never stored by the tool. Evaluating, tuning or proposing asks first; reads and local prompt/dataset edits do not. Use when the user asks about improving a prompt, testing a prompt against examples, or comparing prompt wordings.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'prompt', 'dataset', 'evaluate', 'tune', 'variants', 'propose', 'status' or 'events'"},
                "mode": {"type": "string", "description": "With 'prompt': 'add', 'list', 'get', 'remove' or 'set_current'; with 'dataset': 'add', 'list', 'get' or 'remove'"},
                "prompt_id": {"type": "integer", "description": "With 'prompt' get/remove/set_current / 'evaluate' / 'tune' / 'propose', the prompt number"},
                "name": {"type": "string", "description": "With 'prompt'/'dataset' add, the name"},
                "text": {"type": "string", "description": "With 'prompt' add, the prompt text; with 'evaluate'/'tune'/'propose', an inline prompt text instead of prompt_id"},
                "dataset_id": {"type": "integer", "description": "With 'dataset' get/remove / 'evaluate' / 'tune', the dataset number"},
                "examples": {"type": "string", "description": "With 'dataset' add, the evaluation examples as JSON, e.g. '[{\"input\": \"...\", \"expected\": \"...\"}]'"},
                "metric": {"type": "string", "description": "With 'evaluate'/'tune', 'exact', 'contains' or 'f1' ('f1' if left out)"},
                "candidates": {"type": "string", "description": "With 'tune', candidate prompt numbers or texts as JSON, e.g. '[\"1\", \"2\"]'"},
                "variants": {"type": "integer", "description": "With 'tune', 1 to also try the built-in variant bank on the base prompt"},
                "propose": {"type": "integer", "description": "With 'tune', most LM-proposed rewrites to add (0 if left out, max 4)"},
                "limit": {"type": "integer", "description": "With 'evaluate'/'tune', most examples per candidate (5 if left out, max 20); with 'propose', most rewrites (3 if left out, max 4); with 'prompt'/'dataset' list or 'events', most shown (20 if left out)"},
                "model": {"type": "string", "description": "With 'evaluate'/'tune'/'propose', the Gemini model (the configured model if left out)"},
                "detail": {"type": "integer", "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=prompt_tune,
    )
    # R18-6 (fonoster/fonoster inspiration) — the local voice-agent /
    # call-flow engine + the operator-supplied SIP trunk client joins
    # the Your world group: it answers phone calls with a script the
    # owner writes, through the owner's own SIP trunk.
    registry.register(
        name="voice_agent",
        description=(
            "Answer phone calls with a voice agent the owner builds: "
            "write call scripts (say / gather / branch / transfer / "
            "hangup) and run them on a real phone line through the "
            "operator's own SIP trunk (trunk credentials are supplied "
            "per call, never stored). 'call' and 'serve' ask first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "One of: 'agent' (add/list/get/remove a voice agent), 'flow' (add/list/get/remove a call script), 'run' (run a flow in a local text session), 'call' (place a call through the operator's SIP trunk and run the agent's flow — asks first), 'serve' (register to the trunk and answer inbound calls — asks first), 'stop' (stop the listener), 'status' (counts + listener state + TTS/STT availability) or 'events' (the ledger)"},
                "mode": {"type": "string", "description": "With 'agent'/'flow', one of 'add', 'list', 'get' or 'remove'"},
                "agent_id": {"type": "integer", "description": "With 'agent' get/remove, 'call' or 'serve', the agent number"},
                "name": {"type": "string", "description": "With 'agent'/'flow' add, the name (at most 80 chars)"},
                "greeting": {"type": "string", "description": "With 'agent' add, what the agent says first (at most 4000 chars)"},
                "flow_id": {"type": "integer", "description": "With 'agent' add, the flow the agent runs; with 'flow' get/remove or 'run'/'call', the flow number"},
                "steps": {"type": "string", "description": "With 'flow' add, the flow steps as JSON (say/gather/branch/transfer/hangup, at most 50 steps)"},
                "inputs": {"type": "string", "description": "With 'run', the caller's replies as a JSON list, e.g. [\"1\"]"},
                "number": {"type": "string", "description": "With 'call', the number to dial (digits, +, spaces, dashes, parens, 3-32 chars)"},
                "trunk": {"type": "string", "description": "With 'call'/'serve', the operator's SIP trunk as JSON {host, port, user, pass} — supplied per call, never stored"},
                "transfer_to": {"type": "string", "description": "With 'agent' add or 'call', the number a transfer goes to (the agent's configured transfer_to only)"},
                "timeout_s": {"type": "integer", "description": "With 'call', the most seconds the call may run (120 if left out, max 300)"},
                "limit": {"type": "integer", "description": "With 'events', most events shown (20 if left out, max 100)"},
                "detail": {"type": "integer", "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=voice_agent,
    )
