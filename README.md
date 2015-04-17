pymultiwriter
=============

pymultiwriter is like a CLI version of
[gnome-multi-writer](https://wiki.gnome.org/Apps/MultiWriter). It writes disk
images to several USB disks in parallel. Compared to dd, pymultiwriter is

* Easier: it shows an always up-to-date list of connected USB disks (via udev).
  No need to check `dmesg` for device names, and no need to unmount (it does
  that automatically).
* Safer: you won't accidentally write to your internal disk using
  pymultiwriter: it only shows USB disks.
* Nicer: transfer speed, ETA and some smileys are displayed


The usual statement about liability
-----------------------------------

I'm not responsible if this deletes your hamster. Be careful.
