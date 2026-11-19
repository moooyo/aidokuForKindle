# Source-bound status trial build

[The verification record](build-and-pixels.json) binds the final ARM and desktop
programs to source manifest `1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768`.
The 360 inputs were compared with the qualified cold-cover source. The ARM
binary is `9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f`.
825 library and 22 startup tests passed; the six opt-in or existing library
tests remained ignored. Captures of the demo Library on Scribe and PW5 matched
the entire reference images, not just the area below the clock. The desktop
capture does not run the physical toolbar sampling mechanism. Earlier builds
are not included in the verification record.
