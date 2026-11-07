# ASS collapse corpus

The JSON contains ASS input literals from the pinned SubMiner test source linked in each case,
plus one labelled local same-layer duplicate control. Only fixture data was transcribed; no
upstream implementation or test code is included. Expected counts follow Saitenka’s conservative
contract, so different layers and the 200 ms transform run deliberately differ from upstream.

`poe corpus-lock` pins all 15 complete inputs, provenance and expectations. At this revision they
contain 47 authored events and 36 logical study cues: 11 fewer rows (23.40%) for the warm loop to
traverse. The per-case warm-loop integration test checks the logged traversal count. These units
do not measure tokenization time or imply a general playback speedup.
