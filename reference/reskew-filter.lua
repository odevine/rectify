obs = obslua
bit = require("bit")

-- ReSkew: perspective (keystone) and rotation correction, meant to sit AFTER
-- ReFish in the filter chain. ReFish removes the lens barrel/pincushion;
-- ReSkew removes the keystone left by a camera that is not square to a flat
-- subject. The two are kept separate so each does one job and each calibrates
-- on its own.
--
-- The shader samples the source through a single 3x3 homography. Eight of its
-- entries come from calibrate_perspective.py (paste them into the h.. fields);
-- the rotation, zoom and offset sliders are friendly adjustments composed on
-- top for leveling, cropping and recentring by hand.

TEXT_FILTER_NAME = 'ReSkew'

SETTING_H11 = 'h11'
SETTING_H12 = 'h12'
SETTING_H13 = 'h13'
SETTING_H21 = 'h21'
SETTING_H22 = 'h22'
SETTING_H23 = 'h23'
SETTING_H31 = 'h31'
SETTING_H32 = 'h32'
SETTING_ROTATION = 'rotation'
SETTING_ZOOM = 'zoom'
SETTING_OFFSET_X = 'offset_x'
SETTING_OFFSET_Y = 'offset_y'
SETTING_EDGE_MODE = 'edge_mode'
SETTING_SUPERSAMPLE = 'supersample'

source_def = {}
source_def.id = 'reskew-filter'
source_def.type = obs.OBS_SOURCE_TYPE_FILTER
source_def.output_flags = bit.bor(obs.OBS_SOURCE_VIDEO)

function set_render_size(filter)
    local target = obs.obs_filter_get_target(filter.context)
    local width, height
    if target == nil then
        width = 0
        height = 0
    else
        width = obs.obs_source_get_base_width(target)
        height = obs.obs_source_get_base_height(target)
    end
    filter.width = width
    filter.height = height
end

source_def.get_name = function()
    return TEXT_FILTER_NAME
end

-- Multiply two 3x3 matrices stored row-major as flat 9-tables.
function mat3_mul(a, b)
    local c = {}
    for row = 0, 2 do
        for col = 0, 2 do
            local s = 0
            for k = 0, 2 do
                s = s + a[row * 3 + k + 1] * b[k * 3 + col + 1]
            end
            c[row * 3 + col + 1] = s
        end
    end
    return c
end

source_def.create = function(settings, source)
    local filter = {}
    filter.params = {}
    filter.context = source
    filter.m = {1, 0, 0, 0, 1, 0, 0, 0, 1}

    set_render_size(filter)

    obs.obs_enter_graphics()
    filter.effect = obs.gs_effect_create(shader, nil, nil)
    if filter.effect ~= nil then
        for i = 0, 8 do
            filter.params['m' .. i] = obs.gs_effect_get_param_by_name(filter.effect, 'm' .. i)
        end
        filter.params.edge_mode = obs.gs_effect_get_param_by_name(filter.effect, 'edge_mode')
        filter.params.supersample = obs.gs_effect_get_param_by_name(filter.effect, 'supersample')
        filter.params.texture_width = obs.gs_effect_get_param_by_name(filter.effect, 'texture_width')
        filter.params.texture_height = obs.gs_effect_get_param_by_name(filter.effect, 'texture_height')
    end
    obs.obs_leave_graphics()

    if filter.effect == nil then
        source_def.destroy(filter)
        return nil
    end

    source_def.update(filter, settings)
    return filter
end

source_def.destroy = function(filter)
    if filter.effect ~= nil then
        obs.obs_enter_graphics()
        obs.gs_effect_destroy(filter.effect)
        obs.obs_leave_graphics()
    end
end

source_def.get_width = function(filter)
    return filter.width
end

source_def.get_height = function(filter)
    return filter.height
end

source_def.update = function(filter, settings)
    local h11 = obs.obs_data_get_double(settings, SETTING_H11)
    local h12 = obs.obs_data_get_double(settings, SETTING_H12)
    local h13 = obs.obs_data_get_double(settings, SETTING_H13)
    local h21 = obs.obs_data_get_double(settings, SETTING_H21)
    local h22 = obs.obs_data_get_double(settings, SETTING_H22)
    local h23 = obs.obs_data_get_double(settings, SETTING_H23)
    local h31 = obs.obs_data_get_double(settings, SETTING_H31)
    local h32 = obs.obs_data_get_double(settings, SETTING_H32)
    local rotation = obs.obs_data_get_double(settings, SETTING_ROTATION)
    local zoom = obs.obs_data_get_double(settings, SETTING_ZOOM)
    local offx = obs.obs_data_get_double(settings, SETTING_OFFSET_X)
    local offy = obs.obs_data_get_double(settings, SETTING_OFFSET_Y)

    filter.edge_mode = obs.obs_data_get_int(settings, SETTING_EDGE_MODE)
    filter.supersample = obs.obs_data_get_bool(settings, SETTING_SUPERSAMPLE)
    set_render_size(filter)

    -- Calibrated homography (output uv -> source uv), h33 fixed at 1.
    local H = {h11, h12, h13, h21, h22, h23, h31, h32, 1}

    -- Friendly transform F (output uv -> adjusted uv): rotate and zoom about the
    -- centre in aspect-corrected space, then offset. Affine, so still 3x3.
    local aspect = 1.0
    if filter.height and filter.height > 0 then
        aspect = filter.width / filter.height
    end
    local th = math.rad(rotation)
    local c, s = math.cos(th), math.sin(th)
    local z = zoom
    if z < 0.0001 then z = 0.0001 end
    -- Linear part L = Sx(1/aspect) * R(theta) * Sx(aspect) / zoom.
    local l00 = (c) / z
    local l01 = (-s / aspect) / z
    local l10 = (s * aspect) / z
    local l11 = (c) / z
    -- adjusted = L*(out - centre) + centre + offset
    local cx, cy = 0.5, 0.5
    local tx = cx - (l00 * cx + l01 * cy) + offx
    local ty = cy - (l10 * cx + l11 * cy) + offy
    local F = {l00, l01, tx, l10, l11, ty, 0, 0, 1}

    filter.m = mat3_mul(H, F)
end

source_def.video_render = function(filter, effect)
    if not obs.obs_source_process_filter_begin(filter.context, obs.GS_RGBA, obs.OBS_NO_DIRECT_RENDERING) then
        return
    end

    for i = 0, 8 do
        obs.gs_effect_set_float(filter.params['m' .. i], filter.m[i + 1])
    end
    obs.gs_effect_set_int(filter.params.edge_mode, filter.edge_mode)
    obs.gs_effect_set_bool(filter.params.supersample, filter.supersample)
    obs.gs_effect_set_float(filter.params.texture_width, filter.width)
    obs.gs_effect_set_float(filter.params.texture_height, filter.height)

    obs.obs_source_process_filter_end(filter.context, filter.effect, filter.width, filter.height)
end

source_def.get_properties = function(settings)
    local props = obs.obs_properties_create()

    local p_rot = obs.obs_properties_add_float_slider(props, SETTING_ROTATION, 'Rotation (deg)', -45.0, 45.0, 0.01)
    obs.obs_property_set_long_description(p_rot, 'Level the image. Rotates about the centre.')

    local p_zoom = obs.obs_properties_add_float_slider(props, SETTING_ZOOM, 'Zoom', 0.25, 4.0, 0.001)
    obs.obs_property_set_long_description(p_zoom, 'Crop away the border the correction opens up, or fit more in.')

    local p_ox = obs.obs_properties_add_float_slider(props, SETTING_OFFSET_X, 'Offset X', -0.5, 0.5, 0.001)
    obs.obs_property_set_long_description(p_ox, 'Recentre horizontally after correction.')
    local p_oy = obs.obs_properties_add_float_slider(props, SETTING_OFFSET_Y, 'Offset Y', -0.5, 0.5, 0.001)
    obs.obs_property_set_long_description(p_oy, 'Recentre vertically after correction.')

    -- Homography coefficients. Calibrated, not meant for hand-tuning. Paste the
    -- eight numbers from calibrate_perspective.py; leave at identity otherwise.
    local hint = 'Calibrated perspective term. Paste from calibrate_perspective.py.'
    for _, name in ipairs({SETTING_H11, SETTING_H12, SETTING_H13,
                           SETTING_H21, SETTING_H22, SETTING_H23,
                           SETTING_H31, SETTING_H32}) do
        local p = obs.obs_properties_add_float(props, name, name, -100.0, 100.0, 0.0000001)
        obs.obs_property_set_long_description(p, hint)
    end

    local edge_list = obs.obs_properties_add_list(props, SETTING_EDGE_MODE, 'Edge Mode',
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT)
    obs.obs_property_list_add_int(edge_list, 'Clamp', 0)
    obs.obs_property_list_add_int(edge_list, 'Border (black)', 1)
    obs.obs_property_list_add_int(edge_list, 'Mirror', 2)
    obs.obs_property_set_long_description(edge_list,
        'How to fill pixels pulled from outside the source. Clamp=stretch edge, Border=black, Mirror=reflect.')

    local p_ss = obs.obs_properties_add_bool(props, SETTING_SUPERSAMPLE, 'Supersample (2x2)')
    obs.obs_property_set_long_description(p_ss,
        '2x2 supersample. Reduces aliasing under strong correction. Costs ~4x sampling; off by default.')

    return props
end

source_def.get_defaults = function(settings)
    obs.obs_data_set_default_double(settings, SETTING_H11, 1.0)
    obs.obs_data_set_default_double(settings, SETTING_H12, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_H13, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_H21, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_H22, 1.0)
    obs.obs_data_set_default_double(settings, SETTING_H23, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_H31, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_H32, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_ROTATION, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_ZOOM, 1.0)
    obs.obs_data_set_default_double(settings, SETTING_OFFSET_X, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_OFFSET_Y, 0.0)
    obs.obs_data_set_default_int(settings, SETTING_EDGE_MODE, 0)
    obs.obs_data_set_default_bool(settings, SETTING_SUPERSAMPLE, false)
end

source_def.video_tick = function(filter, seconds)
    set_render_size(filter)
end

function script_description()
    return "Adds a video filter named '" .. TEXT_FILTER_NAME ..
        "' that corrects perspective (keystone) and rotation. Stack it after ReFish."
end

function script_load(settings)
    obs.obs_register_source(source_def)
end

shader = [[
// ReSkew perspective correction. Samples the source through a 3x3 homography
// that maps each output uv to a source uv. The matrix is built on the CPU from
// the calibrated homography and the rotation/zoom/offset adjustments.

uniform float4x4 ViewProj;
uniform texture2d image;

uniform float m0; uniform float m1; uniform float m2;
uniform float m3; uniform float m4; uniform float m5;
uniform float m6; uniform float m7; uniform float m8;
uniform int edge_mode;
uniform bool supersample;
uniform float texture_width;
uniform float texture_height;

sampler_state def_sampler {
    Filter   = Linear;
    AddressU = Clamp;
    AddressV = Clamp;
};

struct VertData {
    float4 pos : POSITION;
    float2 uv  : TEXCOORD0;
};

VertData VSDefault(VertData v_in)
{
    VertData vert_out;
    vert_out.pos = mul(float4(v_in.pos.xyz, 1.0), ViewProj);
    vert_out.uv  = v_in.uv;
    return vert_out;
}

// Map an output uv to a source uv through the homography.
float2 warp(float2 uv)
{
    float x = m0 * uv.x + m1 * uv.y + m2;
    float y = m3 * uv.x + m4 * uv.y + m5;
    float w = m6 * uv.x + m7 * uv.y + m8;
    return float2(x, y) / w;
}

float4 sample_src(float2 uv)
{
    if (edge_mode == 1) {
        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
            return float4(0.0, 0.0, 0.0, 1.0);
        return image.Sample(def_sampler, uv);
    } else if (edge_mode == 2) {
        float2 m = 1.0 - abs(frac(uv * 0.5) * 2.0 - 1.0);
        return image.Sample(def_sampler, m);
    }
    return image.Sample(def_sampler, uv);
}

float4 PSDrawBare(VertData v_in) : TARGET
{
    if (supersample) {
        float2 px = float2(1.0 / texture_width, 1.0 / texture_height) * 0.25;
        float4 c = float4(0.0, 0.0, 0.0, 0.0);
        c += sample_src(warp(v_in.uv + float2(-px.x, -px.y)));
        c += sample_src(warp(v_in.uv + float2( px.x, -px.y)));
        c += sample_src(warp(v_in.uv + float2(-px.x,  px.y)));
        c += sample_src(warp(v_in.uv + float2( px.x,  px.y)));
        return c * 0.25;
    }
    return sample_src(warp(v_in.uv));
}

technique Draw
{
    pass
    {
        vertex_shader = VSDefault(v_in);
        pixel_shader  = PSDrawBare(v_in);
    }
}
]]
