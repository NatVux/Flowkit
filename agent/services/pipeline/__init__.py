"""Server-side pipeline runner: refs -> scene images -> scene videos -> concat for one video.

Ports /fk-gen-refs, /fk-gen-images, /fk-gen-videos and /fk-concat onto the ordinary
request queue. See docs/PIPELINE_RUNNER.md.
"""
