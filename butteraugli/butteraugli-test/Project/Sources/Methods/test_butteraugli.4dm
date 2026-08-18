//%attributes = {"invisible":true,"preemptive":"capable"}

var $p1; $p2; $result : Picture
$result:=butteraugli($p1; $p2)
// With empty pictures, should return empty picture (no crash)
ASSERT:C1129(True:C214; "butteraugli command exists and handles empty input")

var $left; $right : Picture
READ PICTURE FILE:C678(File:C1566("/RESOURCES/left.png").platformPath; $left)
READ PICTURE FILE:C678(File:C1566("/RESOURCES/right.png").platformPath; $right)

$result:=butteraugli($left; $right)