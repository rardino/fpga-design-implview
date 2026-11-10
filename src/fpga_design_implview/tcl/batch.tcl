# Batch entry point used by the implview command line:
#
#   vivado -mode batch -source batch.tcl -tclargs design <dcp> <design.ivd.gz> ?options?
#   vivado -mode batch -source batch.tcl -tclargs device <part> <device.ivdev.gz>
#
# design options: -device <device.ivdev.gz>  also export the device (cache miss)
#                 -routing                   per-tile routing usage
#                 -paths N                   N worst setup and hold paths

source [file join [file dirname [info script]] implview.tcl]

set mode [lindex $argv 0]
set src [lindex $argv 1]
set out [lindex $argv 2]
set opts [lrange $argv 3 end]

if {$mode eq "device"} {
    link_design -part $src
    implview::export_device $out
    return
}

open_checkpoint $src
set i [lsearch -exact $opts -device]
if {$i >= 0} {
    implview::export_device [lindex $opts [expr {$i + 1}]]
    set opts [lreplace $opts $i [expr {$i + 1}]]
}
implview::export_design $out {*}$opts
