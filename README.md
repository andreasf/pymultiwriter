pymultiwriter
=============

pymultiwriter is like a CLI version of
[gnome-multi-writer](https://wiki.gnome.org/Apps/MultiWriter): it writes disk
images to several USB disks in parallel, but doesn't require GTK 3.12 or any
other fancy GUI things! Compared to dd, pymultiwriter is

* Easier: it shows an always up-to-date list of connected USB disks (via udev).
  No need to check `dmesg` for device names, and no need to unmount (it does
  that automatically).
* Safer: you won't accidentally write to your internal disk using
  pymultiwriter: it only shows USB disks.
* Nicer: transfer speed, ETA and some smileys are displayed

Since pymultiwriter needs udev and /proc, it only works on Linux.


The usual statement about liability
-----------------------------------

I'm not responsible if this deletes your hamster. Be careful.
