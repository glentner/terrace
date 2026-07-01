Simple Overlay Service
======================

This project is currently empty sitting on `main` with no code or any existing material.
We only have the name "simple overlay service" as a funny place-holder until a better name
is chosen. The name isn't important.

We are going to build an obvious, clean, reference implementation of a file-system overlay
program in modern Python (3.15+) using all of the available tools and innovations available.
The design goal is to address the challenges of data tiering in research computing workloads.
Right now, in most HPC environments, users (researchers) are manually tiering data as part of
their workflows by staging data from different file systems either directly mounted in the 
Linux VFS or using some special tool (HSI, S3). Many workflow tools even have direct support 
at least for intermediate products in places like /tmp.

In HPC you might find something like /project (main file system), /scratch (high-performance),
/tmp (local), and /dev/shm (RAM disk), as the hierarchy. The top-level /project could be an S3
storage location or paired with some archive format like .tar or .zip aggregate types.

What I want is a clean, reference implementation of what a managed tiering service could look
like as a stand-alone program in Python. We need to implement the overlay with FUSE so that
when it's mounted the users' application can point directly at that and all of the tiering is
managed. We'll need to implement basic POSIX tiers as well as S3 operations. I also want to 
implement at least TAR and ZIP as a location on top of POSIX. These should be configurable
as named tiers in a TOML file at application start and each should have a configurable quota
implemented inside the overlay. So if I pointed at some /project/<space> as the ultimate source,
then some /scratch/<space> (10 TB, 1M files), then /tmp/<space> (512 GB, 1M files), then 
/dev/shm/<space> (64 GB, 1M files) that should mean that we evict the least-recently-used assets
from our staging areas when those quotas are met all the way up the stack.

Because we are also trying to support write-heavy applications like bioinformatics and AI where
there may be random I/O operations for a period then relaxation, we want to also allow some grace
period at each tier that delays committing altered files for some period. For example, if I'm
making heavy writes in the middle of a file, that file should only get written back up the stack
after 60 seconds of inactivity, or at most once every M minutes. You should also be able to specify
that a tier is a pass-through for writes and should never delay (e.g., /dev/shm -> /tmp).

For the archive formats (TAR, ZIP) I was thinking we could allow for both local and remote tiers
to lean on this. So /tmp/<some> -> /scratch/<some> -> /project/some.zip would treat the /scratch
tier as the unzipped version of data and changes can be updated directly into the /project/some.zip
but /tmp/<some> -> /scratch/<some> -> /scratch/some.tar.gz -> s3://some.tar.gz would allow for
periodic re-creations using the /scratch/some.tar.gz as a staging copy along-side the extracted
version and an at-most N refreshes per unit time policy to re-sync to s3:// copy.

The trick for all of this is to lean on the correct abstractions and existing frameworks in Python
to have this be the most elegant realization of this idea as a research project.


Tasks
-----

* Research and create PLAN.md document after aligning on goals and features.
* Draft ROADMAP.md from PLAN.md with enumerated phases and discrete steps.
* Draft AGENTS.md (<- CLAUDE.md symlink) to jump into implementation and project.
* Create /continue and /release agent skills to drive implementation.


Thoughts
--------

* We could make the entry-point, `sosfs` (kinda funny).
* We can certainly test most of this with POSIX for the unit tests. Not sure about stubbing S3 or
  Ram disks.


References
----------

* ../../hypershell/hypershell for code-style and architectural preferences (uv, pyproject.toml).
* ../../purduercac/agentbase for agentic setup with skills and ROADMAP.md implementation plan.
