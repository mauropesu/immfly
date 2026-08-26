P100 Service: teensycfg (display and button customization)

Summary

Some elements of the display and some of the buttons of the p100 are controlled by the Teensy

microcontroller.

This customizations are managed by teensycfg service.

Udev Rules

The following udev rules must be defined on  /etc/udev/rules.d/49-teensy.rules :

1 ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="04[789B]?", ENV{ID_MM_DEVICE_IGNORE}="1",

ENV{ID_MM_PORT_IGNORE}="1"

2 ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="04[789A]?", ENV{MTP_NO_PROBE}="1"

Configuration

The configuration file is located at  /etc/teensyconfig  as a json file:

1 {
2
3
4
5
6
7
8
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
21
22
23
24
25
26 }

"buttons": {

"1": [

"inop"

],
"2": [

"inop"

],
"3": [

"internet"

],
"4": [

"inop"

],
"5": [

"wifi_on",
"wifi_off"

]

},
"callbacks": {

"wifi_off": "led5off",
"wifi_on": "led5on"

},
"wap": "on",
"title": "Ethiopian Airlines"

Buttons

for the buttons, we can assign a list of consecutive actions. In the example above, button 5 has

been configured so that the Wi-Fi is turned on the first time the button is pressed, and turned off

the next time the button is pressed, always at the same order and at the end of the last action it

starts again.

inop  (button will not perform any action)

wifi_on  (button will turn on the Wi-Fi)

wifi_off  (button will turn off the Wi-Fi)

internet  (show internet status on display)

callbacks

Some actions may trigger callbacks.  wifi_on  and  wifi_off  can trigger:

led1on  (Turn on the light around button 1)

led1off  (Turn off the light around button 1)

led2on  (Turn on the light around button 2)

led2off  (Turn off the light around button 2)

led3on  (Turn on the light around button 3)

led3off  (Turn off the light around button 3)

led4on  (Turn on the light around button 4)

led4off  (Turn off the light around button 4)

wap

You can configure the initial status of wap:

on

off

title

You can define which title you want to appear at the bottom of the screen.

Execution

The teensycfg service is managed by upstart (Ubuntu 14.04).

You can control this service as usual:

1 service teensycfg status
2 service teensycfg start
3 service teensycfg stop
4 service teensycfg restart

