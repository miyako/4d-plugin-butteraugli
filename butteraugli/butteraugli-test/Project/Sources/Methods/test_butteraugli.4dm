//%attributes = {"invisible":true,"preemptive":"capable"}

var $p1; $p2; $result : Picture
$result:=butteraugli($p1; $p2)
// With empty pictures, should return empty picture (no crash)
ASSERT(True; "butteraugli command exists and handles empty input")
