# 4d-plugin-butteraugli

Estimate the psycho-visual similarity of two images using [butteraugli](https://github.com/google/butteraugli).

By default, ``.png`` format inside a picture is used. If ``.png`` is not found, ``.jpg`` is used.

### Examples

```
$path:=Get 4D folder(Current resources folder)+"vs_ssimulacra_left.png"
READ PICTURE FILE($path;$left)

$path:=Get 4D folder(Current resources folder)+"vs_ssimulacra_right.png"
READ PICTURE FILE($path;$right)

$status:=butteraugli ($left;$right)

If ($status.success)
	
	$heatmap:=$status.heatmap
	SET PICTURE TO PASTEBOARD($heatmap)
	
End if 

CONVERT PICTURE($left;".jpg")
CONVERT PICTURE($right;".jpg")

$status:=butteraugli ($left;$right)

If ($status.success)
	
	$heatmap:=$status.heatmap
	SET PICTURE TO PASTEBOARD($heatmap)
	
End if 
```
