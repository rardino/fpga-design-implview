# Batch entry point used by the implview command line:
#
#   vivado -mode batch -source batch.tcl -tclargs design <dcp> <design.ivd.gz> ?options?
#   vivado -mode batch -source batch.tcl -tclargs device <part> <device.ivdev.gz>
#
# design options: -device <device.ivdev.gz>  also export the device (cache miss)
#                 -routing                   per-tile routing usage
#                 -detail ?-nets N?          cells, nets and net routes
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

if {$mode eq "crosscheck"} {
    # crosscheck <dcp> <outdir> ?-bit FILE?: the bitstream (unless given) and
    # what the design export leaves out: the pips of the VCC/GND nets, the
    # route status, and ROUTE vs get_pips agreement per net.
    open_checkpoint $src
    file mkdir $out
    set i [lsearch -exact $opts -bit]
    if {$i < 0} {
        foreach rule {NSTD-1 UCIO-1 RTSTAT-10 CHECK-3 PDRC-153} {
            catch {set_property SEVERITY Warning [get_drc_checks $rule]}
        }
        set t0 [clock milliseconds]
        write_bitstream -force [file join $out design.bit]
        puts "implview: bitstream written in [expr {([clock milliseconds] - $t0) / 1000.0}] s"
    }
    set f [open [file join $out const_pips.txt] w]
    foreach n [get_nets -hierarchical -quiet -top_net_of_hierarchical_group -filter {TYPE == POWER || TYPE == GROUND}] {
        foreach p [get_pips -quiet -of_objects $n] { puts $f "[get_property TYPE $n]\t$p" }
    }
    close $f
    set f [open [file join $out route_status.txt] w]
    puts $f [report_route_status -return_string]
    close $f
    # ROUTE property vs get_nodes: every node named in ROUTE is a node of the net
    set f [open [file join $out route_vs_nodes.txt] w]
    set nn 0; set bad 0
    foreach n [get_nets -hierarchical -quiet -top_net_of_hierarchical_group -filter {ROUTE_STATUS == ROUTED && TYPE != POWER && TYPE != GROUND}] {
        set r [get_property ROUTE $n]
        set words 0
        foreach w [regsub -all {[{}]} $r " "] { if {$w ne ""} { incr words } }
        set nodes [llength [get_nodes -quiet -of_objects $n]]
        incr nn
        if {$words != $nodes} { incr bad; puts $f "$n\troute_words=$words\tnodes=$nodes" }
    }
    puts $f "SUMMARY\t$nn\t$bad"
    close $f
    puts "implview: crosscheck data in $out"
    return
}

open_checkpoint $src
set i [lsearch -exact $opts -device]
if {$i >= 0} {
    implview::export_device [lindex $opts [expr {$i + 1}]]
    set opts [lreplace $opts $i [expr {$i + 1}]]
}
implview::export_design $out {*}$opts
