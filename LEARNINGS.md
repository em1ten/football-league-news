# Process learnings (carried over from The Wednesday Times, cross-checked against EFL Feed)

Distilled lessons, not technical reference — see clubs.json / tag_clubs.py / fetch_news.py /
build_site.py comments for the technical detail behind each of these.

## Infrastructure
1. GitHub's own scheduled triggers are unreliable at low volume — needed an external cron
   service (cron-job.org) for consistent timing.
2. `cancel-in-progress: true` is the correct default. Only ever set it `false` temporarily to
   diagnose something else, and remember to revert it.
3. A single stuck workflow run can silently jam everything behind it forever. Renaming the
   concurrency group is a free, fast fix — try it early, not as a last resort.
4. A missing dependency can crash a job before the deploy step with no obvious symptom except
   "nothing's updating." Always check the actual step-by-step log, not just pass/fail.
5. Don't schedule anything on the exact :00/:15/:30/:45 marks — that's when everyone else's
   jobs fire too. [EFL Feed uses 7,37 * * * *]

## Content quality
6. Merge new data with the previous run rather than fully replacing it — one feed temporarily
   failing shouldn't make content visibly vanish. [implemented: fetch_news.py load_previous()]
7. Filtering "is this on-topic" works better as a positive requirement (must mention the
   actual subject) than a growing blocklist. [implemented: tag_clubs.py match_clubs()]
8. Source names need active aliasing — the same publisher shows up under multiple domain
   variants, AND under generated titles (Google News RSS channel titles are the raw search
   query, not "Google News") — and will silently fragment source labels or break logic keyed
   off source name if you don't normalise them. [found live in EFL Feed: the Google-News
   excerpt-blanking check never fired because of this exact gap — fixed in normalise_source()]
9. Test content-cleanup logic against real pasted examples, not assumptions — a
   plausible-sounding rule broke on the first real edge case more than once. [found live in
   EFL Feed: is_empty_excerpt didn't catch a bare-domain remainder until tested against a
   real Google News excerpt shape]

## Design
10. Minimalism needs active pruning, not just restraint when adding — things that seemed
    useful when added still needed removing later once the page was reviewed as a whole.
11. Silent reordering is worse than no ordering — if prioritising by quality/category, make
    it a filter the user chooses, not an invisible sort. [EFL Feed: sorts by published time
    only, no quality tiering — keep it that way]
12. Test dark mode / any theme-dependent feature explicitly — CSS variable coverage gaps
    don't announce themselves. [Football League News has light and dark themes; the
    Chromium test suite checks both at 320px, including measured text contrast]

## Found during the EFL Feed design review (new, not from Wednesday Times)
18. **An author CSS rule that sets `display` silently defeats the `hidden`
    attribute.** `[hidden]{display:none}` is a UA-stylesheet rule, so ANY author
    rule setting `display` on the same element wins regardless of specificity.
    Symptom: JS sets `el.hidden = true`, attribute is visibly set in devtools,
    nothing hides. Fix: scope layout rules as `.thing:not([hidden])`. Caught
    only because the user reported "pill highlights but stories don't filter" --
    jsdom did NOT reproduce it, so local tests passed while the live site was
    broken. Browser-only CSS behaviour needs a real browser to verify.
19. **Filtering to an empty result set needs an explicit empty state.** Follow a
    club with no coverage that day and every group hides, leaving a blank page
    that looks like a crash. Always give the user a route back out.
20. **RSS `<pubDate>` must be RFC-822, not ISO 8601.** Emitting ISO is a spec
    violation -- readers show wrong dates or drop items. `email.utils.format_datetime`.
21. **Apply the theme before first paint, in a blocking `<head>` script.** Theme
    logic at the end of `<body>` means dark-mode users get a white flash on every
    load. Cheap fix, disproportionately large effect on feeling "finished".

## Naming
13. Always search-check a name before committing, however safe it seems — the least
    likely-sounding collisions turned out to be real, multiple times. [done for "EFL Feed":
    no direct collision, closest are unrelated podcasts]
14. Watch for words that imply a capability you don't have — connotation matters even
    without real legal risk.
15. Plain and boring is a legitimate final answer, not a fallback.

## Process
16. Paste real data (live page source, actual screenshots) instead of describing a bug —
    every hard-to-diagnose issue got solved faster with real evidence on the table.
    [confirmed again on EFL Feed: the raw-HTML-in-excerpt bug and the wide-open-picker bug
    were both only fully diagnosed from the actual screenshot]
17. When something's fixed and re-broken multiple times, stop guessing and go get the
    ground truth directly rather than iterating blind.

## Found in the October review (Birmingham "Your clubs" bug)
24. jsdom applies NO CSS, so it can't test anything visual. It reported `hidden` set
    correctly on 71 "Your clubs" rows while `.yc-row{display:flex}` made the browser draw
    all 72. Test show/hide in a real browser (Playwright + headless Chromium) and measure
    what is actually rendered (bounding boxes), not just DOM attributes. The stylesheet now
    opens with `[hidden]{display:none !important}` so no future `display` rule can ever
    defeat `hidden` again -- this bug hit .cluster, .day-group and .yc-row in turn.
25. Never swallow a failure with `|| echo`. The workflow had `permissions: contents: read`,
    so the articles.json commit-back failed on every run for weeks, hidden by
    `git push || echo "nothing to push"`. Merge-with-previous never worked. Failures should
    surface as `::warning::` annotations on the run.
26. Apply the age window to FRESH articles, not just the previous run's. Official club and
    BBC team feeds return weeks of history (Southampton's: 272 entries), which went straight
    onto the page as 27-day-old stories.
27. Catch content farms by their signature, not their name. Mshale and Unisba Media both end
    titles with a random 10-char code like "(7h1uith2Dm)"; the pattern catches the next clone
    too. Likewise, a "headline" that is only a club name ("Walsall FC") is a profile page.
28. Only diagnose from evidence actually received. A pasted Inspector dump arrived empty, the
    diagnosis claimed what it "showed", and an hour went on stale-deploy and cache theories.
    Check live code == expected code first (it was identical) before blaming deploys.

## Found building the Matchday redesign (October 2026)
29. `<dialog>` has the same trap as `[hidden]`: the browser hides a closed dialog with a
    UA-stylesheet `display:none`, so an unscoped `#picker{display:flex}` would draw it
    permanently. Scope any display rule to `#picker[open]`.
30. Esc in a non-empty `type=search` box clears the text first, and the dialog only closes on
    the second press (Chromium and Safari both). Clearing doesn't always fire `input`, so
    listen for the `search` event too. Test for this behaviour instead of fighting it.
31. Keep page CSS and JavaScript out of Python f-strings. Every brace had to be doubled, and
    that's where typos hid. They now live in plain raw-string constants (PAGE_CSS, PAGE_JS)
    dropped into the template whole.
32. Mockup to build: when the build matches the chosen mockup in both themes, show both. A
    dark mockup was approved, but the live page follows each phone's light/dark setting, so
    half of visitors see a light version nobody signed off on unless it's shown too.

## Found fixing the stale form guide (18 October 2026)
33. A warning only printed to a log nobody reads is no warning. Seven football-data team names
    ("Preston", "West Brom", "Peterboro", "Plymouth", "Accrington", "Bristol Rvs", "Crewe")
    never matched a club from day one. The code even said "check the log for UNMATCHED", but
    nobody did, so seven clubs had no form and the positions below them were wrong for weeks.
    Data-quality problems now raise `::warning::` (visible on the run page), and
    test_standings.py pins every real name the source has used.
34. Measure a source's real refresh rate from your own history before relying on it. Going
    through standings.json's git log showed football-data.co.uk lands results 1-3 days late,
    and once sat on the Championship for three weeks. A fast secondary source (ESPN
    scoreboard, last 14 days) now tops it up, merged so each fixture counts once: the
    (home, away) pair is unique in a league season.
35. When a download fails, keep the last good data (lesson 6 again). fetch_standings used to
    drop a division from standings.json on any failure, wiping its table off the page.

## Found adding the Premier League and Top stories (15 November 2026)
36. "Top stories" works when it is decided by who published a story and how many outlets ran
    it, with a fixed cap (one per club per day). It never reads the headline. That is the
    difference from the top-story feature removed after four failures (lesson 11 still holds:
    it is a view the reader chooses, and the page says what the rule is).
37. Demoting junk is not removing it. Live-score and stats pages were marked "low" for two
    months and still made up 36 of 676 stories. If a page has no reporting, drop it.
38. Blocklists need to match how a name actually arrives. "news.bet365.com" was blocked; the
    source came through as "bet365". Match bookmaker names anywhere in the source.
39. New rules should clean saved data too. articles.json is merged with the previous run, so
    load_previous() now re-applies the junk rules instead of waiting four days for age-out.
40. Feeds that fail or come back empty raise one grouped warning (GitHub only shows the first
    ten per step). Leyton Orient sat at zero stories with nothing flagging it.
41. Anything that couldn't be test-fetched when added (the 20 Premier League BBC feeds, their
    football-data names, one fan podcast feed) is marked as such in the code and fails loudly.
