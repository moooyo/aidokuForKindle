# C15 Reserved Baseline Collision Fix

`view.rs.before-reserved-fix` preserves the previously frozen implementation before this authorized follow-up edit. The original three `.before` copies and initial handoff documents remain intact.

The iterative sibling resolver previously allowed a newly generated label to pull an originally noncolliding source into a later rewrite. The follow-up freezes the original actual-name collision membership before refinement. Other baseline labels are reserved and cannot be rewritten. Their real names may still inform a distinguishing token for an original collision member.

Only `src/app/view.rs` changed in this follow-up. The same bounded, already-materialized visible sibling rows are used. No full-library, database, disk, or image work was added. The complete title/name fields, repeated-name handling, Unicode boundaries, and existing numeric/Unicode assertions are retained.

One regression was added: `source_collision_refinement_reserves_noncolliding_baselines`. The actual three-name adversarial case checks that the third label remains byte-for-byte unchanged, all three final display labels differ, the affected middle words remain present, and every complete actual name is retained. There are now seven focused new identity tests across both edits.

This is a static handoff for Root, the independent identity reviewer, and remote FVD. No local formatting, tests, validation, hashing, runtime probe, or image inspection was performed. Successful execution and formatting remain for the remote FVD run with Cargo jobs limited to two.
