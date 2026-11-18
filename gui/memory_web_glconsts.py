# Refactor Phase 1 step 5: moved verbatim from gui/memory_web.py (split by family).



# ---------------------------------------------------------------------------
# The 3D renderer (only defined when Qt's GL stack imported cleanly;
# the container treats its absence as "2D only"). All-Qt OpenGL — no
# PyOpenGL: nodes are GL_POINTS sphere impostors shaded in the
# fragment shader, edges are GL_LINES, and labels are a QPainter
# overlay projected through the same matrices used for picking.
# ---------------------------------------------------------------------------

import ctypes as _ctypes  # noqa: E402  (float arrays for GL buffers)

_GL_FLOAT = 0x1406

_GL_POINTS = 0x0000

_GL_LINES = 0x0001

_GL_COLOR_BUFFER_BIT = 0x00004000

_GL_DEPTH_BUFFER_BIT = 0x00000100

_GL_DEPTH_TEST = 0x00000B71

_GL_BLEND = 0x00000BE2

_GL_SRC_ALPHA = 0x00000302

_GL_ONE_MINUS_SRC_ALPHA = 0x00000303

_GL_PROGRAM_POINT_SIZE = 0x00008642

_POINT_VS = """#version 330
in vec3 aPos;
in float aSize;
in float aMode;
in vec4 aColor;
uniform mat4 uMVP;
uniform mat4 uView;
out vec4 vColor;
out float vMode;
void main() {
    vec4 viewPos = uView * vec4(aPos, 1.0);
    gl_Position = uMVP * vec4(aPos, 1.0);
    float depth = max(1.0, -viewPos.z);
    // aSize arrives pre-scaled by the viewport factor (folded in
    // on the CPU — the float-uniform path is unreliable in
    // PySide6 6.12), so only the depth attenuation happens here.
    gl_PointSize = clamp(aSize / depth, 2.0, 400.0);
    vColor = aColor;
    vMode = aMode;
}
"""

_POINT_FS = """#version 330
in vec4 vColor;
in float vMode;
out vec4 fragColor;
void main() {
    vec2 uv = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(uv, uv);
    if (r2 > 1.0) discard;
    if (vMode > 0.5) {
        // Selection ring: a soft band at the rim, hollow inside.
        float band = smoothstep(0.42, 0.60, r2)
                   * (1.0 - smoothstep(0.88, 1.0, r2));
        if (band <= 0.01) discard;
        fragColor = vec4(vColor.rgb, vColor.a * band);
        return;
    }
    float z = sqrt(max(0.0, 1.0 - r2));
    vec3 n = vec3(uv.x, -uv.y, z);
    vec3 lightDir = normalize(vec3(-0.45, 0.55, 0.75));
    float diff = max(dot(n, lightDir), 0.0);
    vec3 halfVec = normalize(lightDir + vec3(0.0, 0.0, 1.0));
    float spec = pow(max(dot(n, halfVec), 0.0), 48.0) * 0.55;
    float rim = pow(1.0 - z, 2.4) * 0.38;
    vec3 col = vColor.rgb * (0.34 + 0.85 * diff)
             + spec + rim * vColor.rgb;
    float edge = 1.0 - smoothstep(0.92, 1.0, r2);
    fragColor = vec4(col, vColor.a * edge);
}
"""

_LINE_VS = """#version 330
in vec3 aPos;
in vec4 aColor;
uniform mat4 uMVP;
out vec4 vColor;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vColor = aColor;
}
"""

_LINE_FS = """#version 330
in vec4 vColor;
out vec4 fragColor;
void main() {
    fragColor = vColor;
}
"""
