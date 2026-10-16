obs = obslua
bit = require("bit")

TEXT_FILTER_NAME = 'ReFish'
TEXT_K1 = 'Radial k1'
TEXT_K2 = 'Radial k2'
TEXT_K3 = 'Radial k3'
TEXT_P1 = 'Tangential p1'
TEXT_P2 = 'Tangential p2'
TEXT_CENTER_X = 'Center X'
TEXT_CENTER_Y = 'Center Y'
TEXT_SCALE = 'Scale / Zoom'
TEXT_EDGE_MODE = 'Edge Mode'
TEXT_SUPERSAMPLE = 'Supersample (2x2)'

SETTING_K1 = 'k1'
SETTING_K2 = 'k2'
SETTING_K3 = 'k3'
SETTING_P1 = 'p1'
SETTING_P2 = 'p2'
SETTING_CENTER_X = 'center_x'
SETTING_CENTER_Y = 'center_y'
SETTING_SCALE = 'scale'
SETTING_EDGE_MODE = 'edge_mode'
SETTING_SUPERSAMPLE = 'supersample'

source_def = {}
source_def.id = 'refish-filter'
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

source_def.create = function(settings, source)
    local filter = {}
    filter.params = {}
    filter.context = source

    set_render_size(filter)

    obs.obs_enter_graphics()
    filter.effect = obs.gs_effect_create(shader, nil, nil)
    if filter.effect ~= nil then
        filter.params.k1 = obs.gs_effect_get_param_by_name(filter.effect, 'k1')
        filter.params.k2 = obs.gs_effect_get_param_by_name(filter.effect, 'k2')
        filter.params.k3 = obs.gs_effect_get_param_by_name(filter.effect, 'k3')
        filter.params.p1 = obs.gs_effect_get_param_by_name(filter.effect, 'p1')
        filter.params.p2 = obs.gs_effect_get_param_by_name(filter.effect, 'p2')
        filter.params.center_x = obs.gs_effect_get_param_by_name(filter.effect, 'center_x')
        filter.params.center_y = obs.gs_effect_get_param_by_name(filter.effect, 'center_y')
        filter.params.scale = obs.gs_effect_get_param_by_name(filter.effect, 'scale')
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
    filter.k1 = obs.obs_data_get_double(settings, SETTING_K1)
    filter.k2 = obs.obs_data_get_double(settings, SETTING_K2)
    filter.k3 = obs.obs_data_get_double(settings, SETTING_K3)
    filter.p1 = obs.obs_data_get_double(settings, SETTING_P1)
    filter.p2 = obs.obs_data_get_double(settings, SETTING_P2)
    filter.center_x = obs.obs_data_get_double(settings, SETTING_CENTER_X)
    filter.center_y = obs.obs_data_get_double(settings, SETTING_CENTER_Y)
    filter.scale = obs.obs_data_get_double(settings, SETTING_SCALE)
    filter.edge_mode = obs.obs_data_get_int(settings, SETTING_EDGE_MODE)
    filter.supersample = obs.obs_data_get_bool(settings, SETTING_SUPERSAMPLE)

    set_render_size(filter)
end

source_def.video_render = function(filter, effect)
    if not obs.obs_source_process_filter_begin(filter.context, obs.GS_RGBA, obs.OBS_NO_DIRECT_RENDERING) then
        return
    end

    obs.gs_effect_set_float(filter.params.k1, filter.k1)
    obs.gs_effect_set_float(filter.params.k2, filter.k2)
    obs.gs_effect_set_float(filter.params.k3, filter.k3)
    obs.gs_effect_set_float(filter.params.p1, filter.p1)
    obs.gs_effect_set_float(filter.params.p2, filter.p2)
    obs.gs_effect_set_float(filter.params.center_x, filter.center_x)
    obs.gs_effect_set_float(filter.params.center_y, filter.center_y)
    obs.gs_effect_set_float(filter.params.scale, filter.scale)
    obs.gs_effect_set_int(filter.params.edge_mode, filter.edge_mode)
    obs.gs_effect_set_bool(filter.params.supersample, filter.supersample)
    obs.gs_effect_set_float(filter.params.texture_width, filter.width)
    obs.gs_effect_set_float(filter.params.texture_height, filter.height)

    obs.obs_source_process_filter_end(filter.context, filter.effect, filter.width, filter.height)
end

source_def.get_properties = function(settings)
    local props = obs.obs_properties_create()

    local p_k1 = obs.obs_properties_add_float_slider(props, SETTING_K1, TEXT_K1, -2.0, 2.0, 0.001)
    obs.obs_property_set_long_description(p_k1,
        'Primary radial distortion. Negative corrects barrel (fisheye), positive corrects pincushion. Main correction knob.')

    local p_k2 = obs.obs_properties_add_float_slider(props, SETTING_K2, TEXT_K2, -2.0, 2.0, 0.001)
    obs.obs_property_set_long_description(p_k2,
        'Higher-order radial term (r^4). Tune after k1 to fix residual curve near frame edges.')

    local p_k3 = obs.obs_properties_add_float_slider(props, SETTING_K3, TEXT_K3, -2.0, 2.0, 0.001)
    obs.obs_property_set_long_description(p_k3,
        'Highest-order radial term (r^6). Only the extreme corners. Usually leave at 0.')

    local p_p1 = obs.obs_properties_add_float_slider(props, SETTING_P1, TEXT_P1, -0.1, 0.1, 0.0001)
    obs.obs_property_set_long_description(p_p1,
        'Tangential distortion (vertical axis). Corrects lens not parallel to sensor. Small values; leave 0 if unsure.')

    local p_p2 = obs.obs_properties_add_float_slider(props, SETTING_P2, TEXT_P2, -0.1, 0.1, 0.0001)
    obs.obs_property_set_long_description(p_p2,
        'Tangential distortion (horizontal axis). Pairs with p1 for lens-tilt skew. Small values; leave 0 if unsure.')

    local p_cx = obs.obs_properties_add_float_slider(props, SETTING_CENTER_X, TEXT_CENTER_X, 0.0, 1.0, 0.001)
    obs.obs_property_set_long_description(p_cx,
        'Horizontal optical center (0=left, 0.5=middle, 1=right). Shift if lens center is off from frame center.')

    local p_cy = obs.obs_properties_add_float_slider(props, SETTING_CENTER_Y, TEXT_CENTER_Y, 0.0, 1.0, 0.001)
    obs.obs_property_set_long_description(p_cy,
        'Vertical optical center (0=top, 0.5=middle, 1=bottom). Shift if lens center is off from frame center.')

    local p_scale = obs.obs_properties_add_float_slider(props, SETTING_SCALE, TEXT_SCALE, 0.25, 4.0, 0.001)
    obs.obs_property_set_long_description(p_scale,
        'Zoom after correction. Increase to crop away black border the correction creates; decrease to fit more in.')

    local edge_list = obs.obs_properties_add_list(props, SETTING_EDGE_MODE, TEXT_EDGE_MODE,
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT)
    obs.obs_property_list_add_int(edge_list, 'Clamp', 0)
    obs.obs_property_list_add_int(edge_list, 'Border (black)', 1)
    obs.obs_property_list_add_int(edge_list, 'Mirror', 2)
    obs.obs_property_set_long_description(edge_list,
        'How to fill pixels pulled from outside the source. Clamp=stretch edge, Border=black, Mirror=reflect.')

    local p_ss = obs.obs_properties_add_bool(props, SETTING_SUPERSAMPLE, TEXT_SUPERSAMPLE)
    obs.obs_property_set_long_description(p_ss,
        '2x2 supersample. Reduces aliasing/blockiness under strong correction. Costs ~4x sampling; off by default.')

    return props
end

source_def.get_defaults = function(settings)
    obs.obs_data_set_default_double(settings, SETTING_K1, -0.15)
    obs.obs_data_set_default_double(settings, SETTING_K2, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_K3, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_P1, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_P2, 0.0)
    obs.obs_data_set_default_double(settings, SETTING_CENTER_X, 0.5)
    obs.obs_data_set_default_double(settings, SETTING_CENTER_Y, 0.5)
    obs.obs_data_set_default_double(settings, SETTING_SCALE, 1.0)
    obs.obs_data_set_default_int(settings, SETTING_EDGE_MODE, 0)
    obs.obs_data_set_default_bool(settings, SETTING_SUPERSAMPLE, false)
end

source_def.video_tick = function(filter, seconds)
    set_render_size(filter)
end

function script_description()
    return "Adds new video effect filter named '" .. TEXT_FILTER_NAME .. "' to imitate lens distortion"
end

function script_load(settings)
    obs.obs_register_source(source_def)
end

shader = [[
// Adaptation by odevine
// Based on Suslik V's original "UnFish Lens" filter & shader, which is in turn based on
// the Sharpness shader of OBS Studio v27.0.0 and https://github.com/Oncorporation/obs-shaderfilter/
//
// A full rewrite of the original UnFish shader to implement the Brown-Conrady distortion model,
// which is more accurate and has more tuning parameters than the original's model

uniform float4x4 ViewProj;
uniform texture2d image;

uniform float k1;
uniform float k2;
uniform float k3;
uniform float p1;
uniform float p2;
uniform float center_x;
uniform float center_y;
uniform float scale;
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

// Map an output uv to a source uv via Brown-Conrady model.
// Work in aspect-corrected (square) space so distortion stays circular.
float2 distort(float2 uv)
{
    float2 center = float2(center_x, center_y);
    float aspect = texture_width / texture_height;

    float2 d = uv - center;
    d.x *= aspect;

    float r2 = dot(d, d);
    float r4 = r2 * r2;
    float r6 = r4 * r2;

    float radial = 1.0 + k1 * r2 + k2 * r4 + k3 * r6;
    float2 dd = d * radial;

    // Tangential (lens tilt) terms.
    dd.x += 2.0 * p1 * d.x * d.y + p2 * (r2 + 2.0 * d.x * d.x);
    dd.y += p1 * (r2 + 2.0 * d.y * d.y) + 2.0 * p2 * d.x * d.y;

    // Zoom to refill frame after correction.
    dd /= max(scale, 0.0001);

    dd.x /= aspect;
    return center + dd;
}

// Sample with selected edge handling.
float4 sample_src(float2 uv)
{
    if (edge_mode == 1) {
        // Border: out-of-range -> black.
        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
            return float4(0.0, 0.0, 0.0, 1.0);
        return image.Sample(def_sampler, uv);
    } else if (edge_mode == 2) {
        // Mirror via triangle-wave reflect.
        float2 m = 1.0 - abs(frac(uv * 0.5) * 2.0 - 1.0);
        return image.Sample(def_sampler, m);
    }
    // Clamp (sampler default).
    return image.Sample(def_sampler, uv);
}

float4 PSDrawBare(VertData v_in) : TARGET
{
    if (supersample) {
        float2 px = float2(1.0 / texture_width, 1.0 / texture_height) * 0.25;
        float4 c = float4(0.0, 0.0, 0.0, 0.0);
        c += sample_src(distort(v_in.uv + float2(-px.x, -px.y)));
        c += sample_src(distort(v_in.uv + float2( px.x, -px.y)));
        c += sample_src(distort(v_in.uv + float2(-px.x,  px.y)));
        c += sample_src(distort(v_in.uv + float2( px.x,  px.y)));
        return c * 0.25;
    }
    return sample_src(distort(v_in.uv));
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
