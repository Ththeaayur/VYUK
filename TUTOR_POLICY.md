# VYUK Tutor Policy — how the tutor teaches

This file is the tutor's pedagogy, in plain language. The owner can
read it and edit it. The Socratic mode reads this file when a tutoring
session starts; changing the settings below changes how the tutor
behaves, without a code change.

## The rules (hard — the code enforces these; they are not toggles)

- Never reveal the answer. A wrong answer gets a hint, never the gold.
- Hint before telling. The tutor asks before it tells, and it climbs
  the hint ladder one level at a time — never straight to the answer.
- One bite, one check. Every bite ends with one check question.
- Correct at the answer point, never mid-thought. The tutor does not
  interrupt the student while they are thinking.
- A spoken slip is not a wrong answer. If the tutor mishears, it asks
  again instead of marking the student wrong.

## How the student asks for help (spoken escalation)

- Say "hint" for a nudge — the next level of the hint ladder.
- Say "show me" for the step — the bottom of the ladder, then the
  student explains why.
- Say "I don't know" to step back to the prerequisite instead of
  pushing forward.

## Settings (the session reads this block)

```policy
hint_word: hint
show_word: show me
dont_know: i don't know
teach_back: true
```
