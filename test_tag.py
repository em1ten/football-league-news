from tag_clubs import match_clubs, clubs_for_run

cases = [
 "Sheffield Wednesday 7-2 Bromley: Owls hit seven at Hillsborough",
 "Sheffield United appoint Chris Wilder as Blades target promotion",
 "I've been reading the report into the club's finances",
 "Reading FC held to a draw at the Madejski",
 "Fixtures announced for Wednesday night's League One matches",
 "Bristol City sign defender from Bristol Rovers in derby-day deal",
 "New York City FC confirm move for York City striker Alex Newby",
 "Derby County boss John Eustace on the East Midlands derby",
 "League One round-up: Blackpool, Huddersfield, Stockport, Mansfield, Bradford and Notts County all win",
 "Salford Red Devils announce new signing",
 "Wigan Athletic and Oldham Athletic both chase the same forward",
 "AFC Wimbledon beat MK Dons at Plough Lane",
 "Premier League: Arsenal edge Liverpool at the Emirates",
]
for c in cases:
    r = match_clubs(c)
    print(f"{r['scope']:9} {str(r['clubs']):55} | {c[:60]}")

print()
print("rotation slice 0 ->", len(clubs_for_run(3, 0)), "clubs, first 3:", clubs_for_run(3,0)[:3])
