# Export procs for implview. Source this in any Vivado session that has a
# design open (a routed checkpoint, or the implemented design at the end of a
# build) and call:
#
#   implview::export_all <dir> ?-routing? ?-paths N?    design and device into <dir>
#   implview::export_design design.ivd.gz ?-routing? ?-paths N?
#   implview::export_device device.ivdev.gz
#
# -paths N: the N worst setup and N worst hold paths (default 50, 0 for none).
#
# then `implview render <dir>/<top>.ivd.gz` (the device file is looked up
# next to the design file, then in the implview cache).
#
# Both files are tab separated, one record per line, first field is the
# record kind. They are gzip'ed when Vivado's Tcl has zlib (Tcl 8.6 on;
# Vivado 2022.1 has 8.5), plain text otherwise. The Python side
# (implview.device / implview.design) reads them back; keep the two in sync
# when adding fields.

namespace eval ::implview {
    variable format_version 1
}

proc ::implview::_open {path} {
    set f [open $path wb]
    if {[llength [info commands zlib]]} {
        zlib push gzip $f -level 6
    }
    return $f
}

proc ::implview::_part_info {f} {
    set part [get_property PART [current_design]]
    puts $f "part\t$part"
    set p [get_parts -quiet $part]
    if {[llength $p]} {
        foreach k {ARCHITECTURE FAMILY DEVICE PACKAGE SPEED} {
            puts $f "[string tolower $k]\t[get_property -quiet $k $p]"
        }
    }
}

proc ::implview::export_device {path} {
    variable format_version
    set t0 [clock milliseconds]
    set f [_open $path]
    puts $f "#implview-device\t$format_version"
    _part_info $f

    # T name type grid_x grid_y num_sites slr_region
    set tiles [get_tiles]
    set idx 0
    set site_tiles {}
    foreach t $tiles ty [get_property TYPE $tiles] gx [get_property GRID_POINT_X $tiles] \
            gy [get_property GRID_POINT_Y $tiles] ns [get_property NUM_SITES $tiles] \
            sl [get_property SLR_REGION_ID $tiles] {
        puts $f "T\t$t\t$ty\t$gx\t$gy\t$ns\t$sl"
        if {$ns > 0} { lappend site_tiles $idx $t }
        incr idx
    }

    # S tile_index name site_type clock_region is_bonded
    set first_of_type [dict create]
    foreach {i t} $site_tiles {
        set ss [get_sites -quiet -of_objects $t]
        if {![llength $ss]} { continue }
        foreach s $ss st [get_property SITE_TYPE $ss] cr [get_property CLOCK_REGION $ss] \
                b [get_property IS_BONDED $ss] {
            puts $f "S\t$i\t$s\t$st\t$cr\t$b"
            if {![dict exists $first_of_type $st]} { dict set first_of_type $st $s }
        }
    }

    # B site_type bel:bel_type ... (from one representative site of each type)
    dict for {st s} $first_of_type {
        set bels [get_bels -quiet -of_objects $s]
        set rec {}
        if {[llength $bels]} {
            foreach b $bels bt [get_property TYPE $bels] {
                lappend rec "[lindex [split $b /] end]:$bt"
            }
        }
        puts $f "B\t$st\t[join $rec \t]"
    }

    # R name row col top_left_gx top_left_gy bottom_right_gx bottom_right_gy slr
    foreach cr [get_clock_regions -quiet] {
        set tl [get_tiles [get_property TOP_LEFT_TILE $cr]]
        set br [get_tiles [get_property BOTTOM_RIGHT_TILE $cr]]
        set slr [get_slrs -quiet -of_objects $cr]
        puts $f "R\t$cr\t[get_property ROW_INDEX $cr]\t[get_property COLUMN_INDEX $cr]\t[get_property GRID_POINT_X $tl]\t[get_property GRID_POINT_Y $tl]\t[get_property GRID_POINT_X $br]\t[get_property GRID_POINT_Y $br]\t$slr"
    }

    # L name slr_index is_master config_order
    foreach slr [get_slrs -quiet] {
        puts $f "L\t$slr\t[get_property SLR_INDEX $slr]\t[get_property IS_MASTER $slr]\t[get_property CONFIG_ORDER_INDEX $slr]"
    }

    # K package_pin site
    foreach p [get_package_pins -quiet] {
        set s [get_sites -quiet -of_objects $p]
        if {[llength $s]} { puts $f "K\t$p\t[lindex $s 0]" }
    }

    close $f
    puts "implview: device exported to $path in [expr {([clock milliseconds] - $t0) / 1000.0}] s"
}

proc ::implview::_slack {args} {
    set p [get_timing_paths -quiet -max_paths 1 -nworst 1 {*}$args]
    if {![llength $p]} { return "" }
    return [get_property -quiet SLACK $p]
}

proc ::implview::_esc {s} {
    return [string map [list "\\" "\\\\" "\t" "\\t" "\n" "\\n" "\r" ""] $s]
}

proc ::implview::_port_site {port} {
    set pin [get_property -quiet PACKAGE_PIN $port]
    if {$pin eq ""} { return "" }
    set s [get_sites -quiet -of_objects [get_package_pins -quiet $pin]]
    return [lindex $s 0]
}

# The port behind a startpoint/endpoint name, empty for a pin.
proc ::implview::_as_port {name} {
    if {$name eq "" || [string first / $name] >= 0} { return {} }
    if {[catch {get_ports -quiet $name} p]} { return {} }
    return $p
}

# T index check slack requirement datapath logic net levels skew start_clock
#   end_clock group startpoint endpoint
# N index pin site      the pins along the path, in order (ports by name)
# Y index report        report_timing of the path, \ tab newline escaped
proc ::implview::_timing_paths {f n} {
    set idx 0
    foreach check {setup hold} {
        foreach p [get_timing_paths -quiet -$check -max_paths $n -nworst 1] {
            set rec [list T $idx $check]
            foreach k {SLACK REQUIREMENT DATAPATH_DELAY DATAPATH_LOGIC_DELAY DATAPATH_NET_DELAY
                       LOGIC_LEVELS SKEW STARTPOINT_CLOCK ENDPOINT_CLOCK GROUP
                       STARTPOINT_PIN ENDPOINT_PIN} {
                lappend rec [get_property -quiet $k $p]
            }
            puts $f [join $rec \t]
            set sp [get_property -quiet STARTPOINT_PIN $p]
            set port [_as_port $sp]
            if {[llength $port]} { puts $f "N\t$idx\t$sp\t[_port_site $port]" }
            foreach pin [get_pins -quiet -of_objects $p] {
                set c [get_cells -quiet -of_objects $pin]
                puts $f "N\t$idx\t$pin\t[get_property -quiet LOC $c]"
            }
            set ep [get_property -quiet ENDPOINT_PIN $p]
            set port [_as_port $ep]
            if {[llength $port]} { puts $f "N\t$idx\t$ep\t[_port_site $port]" }
            if {![catch {report_timing -of_objects $p -return_string} txt]} {
                puts $f "Y\t$idx\t[_esc [string trim $txt]]"
            }
            incr idx
        }
    }
    return $idx
}

proc ::implview::export_all {dir args} {
    file mkdir $dir
    set dev [file join $dir "[get_property PART [current_design]].ivdev.gz"]
    if {![file exists $dev]} { export_device $dev }
    export_design [file join $dir "[get_property TOP [current_design]].ivd.gz"] {*}$args
}

proc ::implview::export_design {path args} {
    variable format_version
    set routing [expr {[lsearch -exact $args -routing] >= 0}]
    set npaths 50
    set i [lsearch -exact $args -paths]
    if {$i >= 0} { set npaths [lindex $args [expr {$i + 1}]] }

    set t0 [clock milliseconds]
    set f [_open $path]
    puts $f "#implview-design\t$format_version"
    _part_info $f
    puts $f "top\t[get_property TOP [current_design]]"
    puts $f "date\t[clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
    puts $f "vivado\t[version -short]"
    puts $f "wns\t[_slack -setup]"
    puts $f "whs\t[_slack -hold]"

    # primitives that need a site (all but the VCC/GND drivers): more than the
    # U records below when the checkpoint is not placed (synthesized netlist)
    set prims [get_cells -hierarchical -quiet -filter {IS_PRIMITIVE && REF_NAME != VCC && REF_NAME != GND}]
    puts $f "primitives\t[llength $prims]"

    # U site bel ref_name primitive_group cell
    set cells [filter -quiet $prims {LOC != ""}]
    if {[llength $cells]} {
        foreach c $cells l [get_property LOC $cells] b [get_property BEL $cells] \
                r [get_property REF_NAME $cells] g [get_property PRIMITIVE_GROUP $cells] {
            puts $f "U\t$l\t[lindex [split $b .] end]\t$r\t$g\t$c"
        }
    }

    # O site port cell: the site of each connected top-level port, with a cell
    # on its net for the color. The N side of a differential input holds no
    # cell (the buffer sits on the P side), but its pad is used.
    foreach p [get_ports -quiet -filter {PACKAGE_PIN != ""}] {
        set s [get_sites -quiet -of_objects [get_package_pins -quiet [get_property PACKAGE_PIN $p]]]
        set c [lindex [get_cells -quiet -of_objects [get_pins -quiet -leaf -of_objects [get_nets -quiet -of_objects $p]]] 0]
        if {[llength $s] && [llength $c]} { puts $f "O\t[lindex $s 0]\t$p\t$c" }
    }

    # X pblock grid_ranges
    foreach pb [get_pblocks -quiet] {
        puts $f "X\t$pb\t[get_property -quiet GRID_RANGES $pb]"
    }

    # the worst setup and hold paths: a failure here leaves the rest of the export
    if {$npaths > 0} {
        if {[catch {_timing_paths $f $npaths} n]} {
            puts "implview: warning: timing paths not exported: $n"
        } else {
            puts "implview: $n timing paths"
        }
    }

    # P tile used_pips (routing usage per tile)
    if {$routing} {
        # every hierarchy level has its own <const0>/<const1> net, and each
        # one returns the whole tie-off routing: leave the constants out
        set nets [get_nets -hierarchical -quiet -top_net_of_hierarchical_group \
                -filter {ROUTE_STATUS == ROUTED && TYPE != POWER && TYPE != GROUND}]
        set count [dict create]
        set n [llength $nets]
        for {set k 0} {$k < $n} {incr k 10000} {
            set pips [get_pips -quiet -of_objects [lrange $nets $k [expr {$k + 9999}]]]
            foreach p $pips {
                dict incr count [string range $p 0 [expr {[string first / $p] - 1}]]
            }
        }
        dict for {t c} $count { puts $f "P\t$t\t$c" }
    }

    close $f
    puts "implview: design exported to $path in [expr {([clock milliseconds] - $t0) / 1000.0}] s"
}
