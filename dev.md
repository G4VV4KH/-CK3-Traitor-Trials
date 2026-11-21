# Developing Traitor Trials

The runtime lives at the root of this repository. The public descriptor uses
the title Traitor Trials and targets CK3 1.21. The English canonical store copy
is in publishing/description.en.md. The gallery contains captures from an
English CK3 session.

The two decisive war-result hooks in common/on_action collect defendants from
the losing side. The events open the royal court for eligible rulers and use
a character event for other rulers. The supported title picker contains the
open and close callbacks that advance the docket. When rebasing that GUI,
preserve the callbacks and exercise Send and Cancel in the game.

The title picker is adapted from Paradox Interactive's CK3 GUI. The optional
MIV integration recognizes the Oathbreaker opinion defined by More Interactive
Vassals and mirrors its legal flags for standalone database loading.

Run the portable static check from the repository root:

~~~sh
python -B tests/check_source.py --source .
~~~

Before changing gameplay, exercise a multi-rebel refusal war in vanilla and
with More Interactive Vassals. Check a title transfer, cancellation, progression
to the next distinct defendant, and manual pause through the final event. The
0.1.0 release inherits gameplay evidence from CK3 1.21.0.3 and MIV 5.7. The
localization check covers all nine game languages. Multiplayer and reloading
in the middle of a hearing were outside the acceptance scope.
