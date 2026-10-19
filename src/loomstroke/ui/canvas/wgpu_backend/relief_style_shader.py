"""Shared bounded profile evaluation and additive multi-light relief shading."""

RELIEF_WGSL = """
fn style_profile(value: f32) -> f32 {
    let x = clamp(value, 0., 1.);
    for (var i=0; i<31; i++) {
        if i+1 >= i32(params.profile_info.x) { break; }
        let a = params.profile_points[i];
        let b = params.profile_points[i+1];
        if x > b.x { continue; }
        let width = b.x-a.x;
        let t = clamp((x-a.x)/width,0.,1.);
        if params.profile_info.y < .5 { return mix(a.y,b.y,t); }
        let t2=t*t; let t3=t2*t;
        let y=(2.*t3-3.*t2+1.)*a.y + (t3-2.*t2+t)*width*a.z
              + (-2.*t3+3.*t2)*b.y + (t3-t2)*width*b.z;
        return clamp(y,min(a.y,b.y),max(a.y,b.y));
    }
    return x;
}
struct ReliefResult { weights: vec2f, highlight: vec4f, tint: vec4f };
fn style_relief(p: vec2i, alpha: f32) -> ReliefResult {
    let dx=(style_profile(style_height_offset(p,vec2f(1,0)))
           -style_profile(style_height_offset(p,vec2f(-1,0))))*.5;
    let dy=(style_profile(style_height_offset(p,vec2f(0,1)))
           -style_profile(style_height_offset(p,vec2f(0,-1))))*.5;
    var direction=select(1.,-1.,params.geometry.y>.5);
    if params.geometry.x>2.5 { direction*=select(1.,-1.,alpha>=.5); }
    let normal=normalize(vec3f(-vec2f(dx,dy)*params.style.x*direction,1.));
    var high=0.; var low=0.; var energy=0.;
    var high_color=vec4f(0.); var diffuse_color=vec4f(0.);
    for (var i=0; i<8; i++) {
        if i>=i32(params.lights_info.x) { break; }
        let source=params.light_directions[i];
        if source.z<=0. { continue; }
        let light=vec3f(cos(source.x)*cos(source.y),-sin(source.x)*cos(source.y),sin(source.y));
        var positive=0.;
        if params.material.z>.5 {
            let diffuse=max(dot(normal,light),0.)*source.z;
            energy+=diffuse;
            diffuse_color+=style_reflectance(params.light_colors[i])*diffuse;
            let halfway=normalize(light+vec3f(0.,0.,1.));
            positive=params.material.x*pow(max(dot(normal,halfway),0.),params.material.y)*source.z;
        } else {
            let directional=dot(normal.xy,light.xy)*source.z;
            positive=max(directional,0.); low+=max(-directional,0.);
        }
        high+=positive;
        high_color+=style_light_color(params.color,params.light_colors[i])*positive;
    }
    var tint=vec4f(1.);
    if params.material.z>.5 {
        let total=params.lighting.z+params.lighting.w*energy;
        low=1.-clamp(total,0.,1.);
        if total>.000001 {
            tint=(vec4f(params.lighting.z)+params.lighting.w*diffuse_color)/total;
        }
    }
    var highlight=params.color;
    if high>.000001 { highlight=high_color/high; }
    return ReliefResult(vec2f(high,low),highlight,tint);
}
"""
