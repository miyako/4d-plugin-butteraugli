#include "butteraugli-4dplugin.h"
#include "butteraugli/butteraugli.h"
#include "stb_image.h"
#include "stb_image_write.h"

// Find a picture representation by type identifier
static PA_long32 get_picture_index(PA_Picture p, const char *type) {
    PA_long32 i = 0;
    if (!p) return 0;
    PA_ErrorCode err = eER_NoErr;
    while (err == eER_NoErr) {
        PA_Unistring utype = PA_GetPictureData(p, ++i, NULL);
        err = PA_GetLastError();
        if (err != eER_NoErr) break;
        uint32_t len = (uint32_t)(utype.fLength * 4) + 1;
        std::vector<uint8_t> buf(len);
        PA_ConvertCharsetToCharset(
            (char *)utype.fString, utype.fLength * sizeof(PA_Unichar), eVTC_UTF_16,
            (char *)&buf[0], len, eVTC_UTF_8);
        std::string uti((const char *)&buf[0]);
        size_t pos = 0, found = 0;
        for (pos = uti.find(';'); pos != std::string::npos; pos = uti.find(';', found)) {
            if (uti.substr(found, pos - found) == type) return i;
            found = pos + 1;
        }
        if (uti.substr(found) == type) return i;
    }
    return 0;
}

// Extract raw image bytes from a 4D Picture
static bool extract_image_data(PA_Picture pic, std::vector<uint8_t> &out) {
    if (!pic) return false;
    
    const char *types[] = {"image/png", ".png", "image/jpeg", ".jpg", NULL};
    PA_long32 idx = 0;
    for (int t = 0; types[t]; t++) {
        idx = get_picture_index(pic, types[t]);
        if (idx) break;
    }
    if (!idx) return false;
    
    PA_Handle h = PA_NewHandle(0);
    PA_GetPictureData(pic, idx, h);
    if (PA_GetLastError() != eER_NoErr) {
        PA_DisposeHandle(h);
        return false;
    }
    uint8_t *data = (uint8_t *)PA_LockHandle(h);
    size_t size = PA_GetHandleSize(h);
    out.assign(data, data + size);
    PA_UnlockHandle(h);
    PA_DisposeHandle(h);
    return true;
}

// Decode image bytes to float RGB channels with gamma correction
static bool decode_to_rgb(const std::vector<uint8_t> &data, int &w, int &h,
                          std::vector<butteraugli::ImageF> &rgb) {
    int channels;
    unsigned char *pixels = stbi_load_from_memory(data.data(), (int)data.size(), &w, &h, &channels, 4);
    if (!pixels) return false;
    
    rgb.clear();
    rgb.push_back(butteraugli::ImageF(w, h));
    rgb.push_back(butteraugli::ImageF(w, h));
    rgb.push_back(butteraugli::ImageF(w, h));
    
    for (int y = 0; y < h; y++) {
        float *row_r = rgb[0].Row(y);
        float *row_g = rgb[1].Row(y);
        float *row_b = rgb[2].Row(y);
        for (int x = 0; x < w; x++) {
            int idx = (y * w + x) * 4;
            row_r[x] = 255.0f * powf(pixels[idx + 0] / 255.0f, 2.2f);
            row_g[x] = 255.0f * powf(pixels[idx + 1] / 255.0f, 2.2f);
            row_b[x] = 255.0f * powf(pixels[idx + 2] / 255.0f, 2.2f);
        }
    }
    stbi_image_free(pixels);
    return true;
}

// Convert diffmap to heatmap PNG
static std::vector<uint8_t> diffmap_to_png(const butteraugli::ImageF &diffmap, int w, int h) {
    // Find max value for normalization
    float maxval = 0.0f;
    for (int y = 0; y < h; y++) {
        const float *row = diffmap.Row(y);
        for (int x = 0; x < w; x++) {
            if (row[x] > maxval) maxval = row[x];
        }
    }
    if (maxval <= 0.0f) maxval = 1.0f;
    
    // Create RGB heatmap
    std::vector<uint8_t> pixels(w * h * 3);
    for (int y = 0; y < h; y++) {
        const float *row = diffmap.Row(y);
        for (int x = 0; x < w; x++) {
            float v = row[x] / maxval;
            if (v < 0.0f) v = 0.0f;
            if (v > 1.0f) v = 1.0f;
            
            uint8_t r, g, b;
            if (v < 0.5f) {
                float t = v * 2.0f;
                r = (uint8_t)(255 * t);
                g = (uint8_t)(255 * t * 0.5f);
                b = 0;
            } else {
                float t = (v - 0.5f) * 2.0f;
                r = 255;
                g = (uint8_t)(128 + 127 * t);
                b = (uint8_t)(255 * t);
            }
            int idx = (y * w + x) * 3;
            pixels[idx + 0] = r;
            pixels[idx + 1] = g;
            pixels[idx + 2] = b;
        }
    }
    
    // Encode to PNG using stb callback
    std::vector<uint8_t> png_out;
    stbi_write_png_to_func([](void *ctx, void *data, int size) {
        auto *out = (std::vector<uint8_t> *)ctx;
        out->insert(out->end(), (uint8_t *)data, (uint8_t *)data + size);
    }, &png_out, w, h, 3, pixels.data(), w * 3);
    
    return png_out;
}

void PluginMain(PA_long32 selector, PA_PluginParameters params) {
    switch (selector) {
        case 1:
            do_butteraugli(params);
            break;
    }
}

static void do_butteraugli(PA_PluginParameters params) {
    PA_Picture pic1 = PA_GetPictureParameter(params, 1);
    PA_Picture pic2 = PA_GetPictureParameter(params, 2);
    
    // Extract image data
    std::vector<uint8_t> data1, data2;
    if (!extract_image_data(pic1, data1) || !extract_image_data(pic2, data2)) {
        PA_ReturnPicture(params, NULL);
        return;
    }
    
    // Decode to float RGB
    int w1, h1, w2, h2;
    std::vector<butteraugli::ImageF> rgb1, rgb2;
    if (!decode_to_rgb(data1, w1, h1, rgb1) || !decode_to_rgb(data2, w2, h2, rgb2)) {
        PA_ReturnPicture(params, NULL);
        return;
    }
    
    // Images must be same size
    if (w1 != w2 || h1 != h2) {
        PA_ReturnPicture(params, NULL);
        return;
    }
    
    // Run butteraugli comparison
    butteraugli::ImageF diffmap;
    double diffvalue;
    if (!butteraugli::ButteraugliInterface(rgb1, rgb2, 1.0f, diffmap, diffvalue)) {
        PA_ReturnPicture(params, NULL);
        return;
    }
    
    // Convert diffmap to heatmap PNG
    std::vector<uint8_t> png_data = diffmap_to_png(diffmap, w1, h1);
    if (png_data.empty()) {
        PA_ReturnPicture(params, NULL);
        return;
    }
    
    // Return as 4D Picture
    PA_Picture result = PA_CreatePicture((void *)png_data.data(), (PA_long32)png_data.size());
    PA_ReturnPicture(params, result);
//    PA_DisposePicture(result);
}
