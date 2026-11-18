# VYUK Spoken Maths — English table

This file is how the tutor speaks maths in English, in plain language.
The Socratic mode reads this file when a tutoring session starts; the
speech strings below are the CANONICAL spoken form of each maths form
— one way of saying it, so the tutor always says it the same way, and
so a spoken answer can be graded against the same form.

## The canonical forms (English)

- A fraction is spoken "numerator over denominator": 7/3 is
  "seven over three". Never "seven slash three".
- A power is spoken by its name: x squared, x cubed, x to the
  power of four. Never "x caret two".
- A product with pi is spoken with the number first: 32 pi is
  "thirty-two pi".
- A derivative is spoken as what it is: d/dx is "d d x"; d/dx of
  x squared is "the derivative of x squared with respect to x".
- An integral is spoken with its limits: the integral from zero to
  one of x d x. A double integral, a triple integral, a line
  integral and a surface integral each say their own name.
- The divergence of F, the curl of F, and the square root of x
  are spoken as words, never as symbols.

## Settings (the session reads this block)

The block below is the table the code reads. Edit the words here
and the tutor speaks and grades differently, without a code change.
A second language is a second file (SPOKEN_MATHS_HI.md, and so on)
with the same shape — the grader reads the table, it never has
English hardcoded into it.

```spoken-maths
{
  "language": "en",
  "ones": {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19
  },
  "tens": {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90
  },
  "scales": {
    "hundred": 100, "thousand": 1000, "million": 1000000
  },
  "point": "point",
  "negative": ["negative", "minus"],
  "fraction_words": ["over", "divided by", "out of"],
  "ordinal_denominators": {
    "half": 2, "halves": 2,
    "third": 3, "thirds": 3,
    "quarter": 4, "quarters": 4,
    "fifth": 5, "fifths": 5,
    "sixth": 6, "sixths": 6,
    "seventh": 7, "sevenths": 7,
    "eighth": 8, "eighths": 8,
    "ninth": 9, "ninths": 9,
    "tenth": 10, "tenths": 10
  },
  "power_words": {
    "squared": 2, "cubed": 3
  },
  "power_phrase": "to the power of",
  "constants": {
    "pi": "pi"
  },
  "operators": {
    "plus": "+", "minus": "-", "times": "*", "multiplied by": "*"
  },
  "variables": ["x", "y", "z", "t", "theta"],
  "units": [
    "metre", "metres", "meter", "meters",
    "second", "seconds", "minute", "minutes",
    "radian", "radians", "degree", "degrees",
    "unit", "units"
  ],
  "symbols": {
    "integral": "the integral",
    "double integral": "the double integral",
    "triple integral": "the triple integral",
    "line integral": "the line integral",
    "surface integral": "the surface integral",
    "divergence of": "the divergence of",
    "curl of": "the curl of",
    "square root of": "the square root of"
  },
  "derivative_words": {
    "derivative": "the derivative of",
    "partial": "the partial derivative of",
    "with respect to": "with respect to",
    "partial bare": "partial"
  },
  "integral_words": {
    "integral": "the integral",
    "from": "from",
    "to": "to",
    "of": "of",
    "over": "over",
    "along": "along"
  }
}
```
