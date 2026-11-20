Bring-up placeholder root policies for zeus (iPhone17,2, iOS 26.5.2 23F84): OS, SystemOS and App.

Each 544-byte manifest carries only the real image sha256 at offset 56 (the builder's digest check); the 395-byte
anchor is the same placeholder as the 27 bring-up policies. The zeus kernel runs with NATIVE_VERIFIED_BLOCK=0, so the
signatures are never read. Replace with real ones (UniXNU scripts/make-root-policies.py) once the root boots.
