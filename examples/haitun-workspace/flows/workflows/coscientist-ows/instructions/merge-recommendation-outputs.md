Merge the four slot-local candidate deltas into the initialized `tmp/candidates`
directory and the four knowledge deltas into the initialized `tmp/knowledge`
directory. Preserve every slot directory, and reject conflicting paths instead
of overwriting one recommender's output with another's.

Treat a knowledge delta whose manifest says `captured=false` as a present,
empty input, not as a missing recommender output.
