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

* Left

![vs_ssim_left](https://user-images.githubusercontent.com/1725068/76504512-37588000-648b-11ea-8fc9-48a246067a0d.png)

* Right

![vs_ssim_right](https://user-images.githubusercontent.com/1725068/76504553-4808f600-648b-11ea-8251-7a35a2d08da8.png)

* Heatmap

![vs_ssim_butteraugli_heatmap](https://user-images.githubusercontent.com/1725068/76504576-5525e500-648b-11ea-99d9-00d27eeec583.png)

