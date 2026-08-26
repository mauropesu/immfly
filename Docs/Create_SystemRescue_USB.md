🔧 Creating a System Rescue USB

🧰 Required Materials

✅ 1x USB drive (32 GB or 64 GB)

✅ 1x Folder with the rescue script and ISO file

Folder

📥 Step 1 — Prepare the Files

1. Download the provided  .zip  file containing the rescue utility.

2. Unzip the file. A folder will be extracted (e.g.,  systemrescueUSB ).

3. Inside this folder, ensure you have:

comando.txt

gen.sh

systemrescue-9.06-amd64.iso

💾 Step 2 — Format the USB Drive

⚠ This process will erase all contents of the USB.

1. Plug the USB into your Linux PC.

2. Run the following to identify the USB device:

lsblk

Look for your USB device (e.g.,  sda ,  sdb ) based on size.

3. Launch a partition tool such as GParted:

sudo gparted

Delete all existing partitions on the USB.

Create a single partition formatted as ext4.

Apply changes and close the tool.

4. Unplug and replug the USB.

5. Run  lsblk  again to confirm the correct device name (e.g.,  /dev/sdb ).

🛠 Step 3 — Generate the Rescue USB

1. Open a terminal.

2. Navigate to the extracted folder:

cd /path/to/systemrescueUSB

3. Run the generation script (replace  sda  with your USB device name):

sudo ./gen.sh sda systemrescue-9.06-amd64.iso 906

⏱ This may take a few minutes.

When it finishes, your System Rescue USB will be ready.

📝 Step 4 — Modify GRUB Configuration (Optional)

After creation, you may need to edit the GRUB configuration:

1. Mount the first partition of the USB:

sudo mount /dev/sda1 /mnt

2. Navigate to the GRUB directory and edit the relevant config file as needed:

sudo nano /mnt/boot/grub/grub.cfg

Remove all the content, then paste the next one:

1 # Global options
2 set timeout=30
3 set default=0
4 set fallback=1
5 set pager=1
6
7 # Display settings
8 if loadfont /boot/grub/font.pf2 ; then
9
10
11
12
13
14
15
16
17
18
19
20
21 fi
22

set gfxmode=640x480
set color_normal=black/cyan
set color_highlight=black/light-gray
set menu_color_normal=black/cyan
set menu_color_highlight=black/light-gray
insmod efi_gop
insmod efi_uga
insmod gfxterm
insmod all_video
insmod videotest
insmod videoinfo
terminal_output gfxterm

23 # enable serial console with common settings (ttyS0, 115200 Baud, 8n1)
24 # this works in parallel to regular console
25 serial --unit=3 --speed=115200 --word=8 --parity=no --stop=1
26 terminal_input --append serial
27 terminal_output --append serial
28
29 menuentry "SystemRescue EQX" {
set gfxpayload=keep
30
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906
31

iomem=relaxed console=ttyS3,115200n8 copytoram

32

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

33 }
34
35 menuentry "SystemRescue P100" {
set gfxpayload=keep
36
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906
37

iomem=relaxed console=ttyS0,115200n8 copytoram

38

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

39 }
40
41 menuentry "Boot SystemRescue using default options" {
42
43

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed

44

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

45 }
46
47 menuentry "Boot SystemRescue and copy system to RAM (copytoram)" {
48
49

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed copytoram

50

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

51 }
52
53 menuentry "Boot SystemRescue and verify integrity of the medium (checksum)" {
54
55

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed checksum

56

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

57 }
58
59 menuentry "Boot SystemRescue using basic display drivers (nomodeset)" {
60
61

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed nomodeset

62

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

63 }
64
65 menuentry "Boot SystemRescue with serial console (ttyS0,115200n8)" {
66
67

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed console=tty0 console=ttyS0,115200n8

68

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

69 }
70
71 menuentry "Boot SystemRescue, do not activate md raid or lvm (nomdlvm)" {
72
73

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed nomdlvm

74

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

75 }
76
77 menuentry "Boot a Linux operating system installed on the disk (findroot)" {
78
79

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed findroot

80

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

81 }
82
83 menuentry "Stop during the boot process before mounting the root filesystem" {
84
85

set gfxpayload=keep
linux /sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE906

iomem=relaxed break

86

initrd /sysresccd/boot/intel_ucode.img /sysresccd/boot/amd_ucode.img

/sysresccd/boot/x86_64/sysresccd.img

insmod fat
insmod chain
terminal_output console
chainloader /EFI/shell.efi

insmod fat
set gfxpayload=800x600,1024x768
linux /EFI/memtest.efi keyboard=both

87 }
88
89 menuentry "Memtest86+ memory tester for UEFI" {
90
91
92
93 }
94
95 menuentry "Start EFI Shell" {
96
97
98
99
100 }
101
102 menuentry "EFI Firmware setup" {
103
104 }
105
106 menuentry "Reboot" {
107
reboot
108 }
109
110 menuentry "Power off" {
111
112 }

fwsetup

halt

