This is a standard covering the material data storage using only two textures in resource packs, as well as the decoding done by shader packs. They can use this data for physically based rendering (PBR), but be aware that normal and specular textures themselves are not directly related to PBR in any way.

This format has been created in the shaderLABS Discord (explaining the name) on the end of April 2019, and improved since. It is intended to replace all previous formats in order to achieve a more consistent support across different shader and resource packs.

In order to simplify the transition on the texture artists' end, a [https://github.com/flodri/RGBA-Formats-Converter/releases/tag/0.4 converter] exists, allowing for an effortless transition from most older formats.

== Normal Texture (_n) ==
The normal vector should be encoded in DirectX format (Y-); this is commonly referred to as top-down normals, which can be visibly characterized as having X/red pointing to the right, and Y/green pointing downward.

=== Red Channel ===
Stores X-axis data, representing surface normal orientation from (0) left to (255) right.

=== Green Channel ===
Stores Y-axis data, representing surface normal orientation from (0) up to (255) down.

=== Blue Channel ===
While this usually stores the Z-axis data, in LabPBR we reconstruct the (<code>.z</code>) of the normal vector using <code>sqrt(1.0 - dot(normal.xy, normal.xy))</code>. 

Represents material ambient occlusion.

* This is stored linearly; A value of 0 results in 100% occlusion and a value of 255 in 0% occlusion.

=== Alpha Channel ===

* Represents height/displacement.
** This is stored linearly; A value of 0 results in a depth of 25% in the texture and a value of 255 in 0% depth.

'''For Texture Artists'''<br />Be aware that a value of 0 on the height map will cause some issues with certain shader packs' POM implementation, so a minimum value of 1 is recommended instead.

=== Reasoning for this Layout ===
The AO is stored in the blue channel because the first 3 components of a pixel in the normal texture represent a vector of length 1. Since we know the length, we only need 2 of the 3 components to reconstruct the vector (thanks Pythagore). This means that one of the three channels can be used for something else, like storing AO in the blue channel.
== Specular Texture (_s) ==
Note that your shader pack is only required to correctly read the smoothness (red) and F0 (green) data to be LabPBR-ready. The other specified datasets are optional components. More details about that can be found in the [[LabPBR Implementation Requirements]].

=== Red Channel ===
* Represents "perceptual" smoothness.
** A value of 255 results in 100% smoothness and a value of 0 in 0% smoothness.
** Convert perceptual smoothness to linear roughness with <code>roughness = pow(1.0 - perceptualSmoothness, 2.0)</code>.
** Convert linear roughness to perceptual smoothness with <code>perceptualSmoothness = 1.0 - sqrt(roughness)</code>.
'''For Texture Artists'''<br />Also known as Glossiness. It is simply inverted Roughness.

=== Green Channel ===
* Values from 0 to 229 represent F0, also known as reflectance.
** This attribute is stored linearly; Please note that a value of 229 represents exactly 229 divided by 255, or approximately 90%, instead of 100%.
* Values from 230 to 255 represent various different metals.  
** Details about this range is provided below.

==== How metals work ====
In order to allow a more accurate representation of metals with the limited amount of information that can be provided, certain metals have been predefined and are selected by setting the green channel to specific values ranging from 230 to 254. In these cases, the albedo is used to tint the reflections instead of being used for diffuse shading.  

If you want a metal that isn't among the predefined metals, you can also set the green channel to a value of 255. In this case, the albedo will instead be used as the F0. This is less accurate, but often still gives decent results.

Note that certain shader packs may not support these predefined metals, and will treat the entire range from 230 to 255 as though it had a value of 255.

{| class="wikitable"
!Metal
!Bit Value
!N (R, G, B)
!K (R, G, B)
|-
|Iron
|230
|2.9114, 2.9497, 2.5845
|3.0893, 2.9318, 2.7670
|-
|Gold
|231
|0.18299, 0.42108, 1.3734
|3.4242, 2.3459, 1.7704
|-
|Aluminum
|232
|1.3456, 0.96521, 0.61722
|7.4746, 6.3995, 5.3031
|-
|Chrome
|233
|3.1071, 3.1812, 2.3230
|3.3314, 3.3291, 3.1350
|-
|Copper
|234
|0.27105, 0.67693, 1.3164
|3.6092, 2.6248, 2.2921
|-
|Lead
|235
|1.9100, 1.8300, 1.4400
|3.5100, 3.4000, 3.1800
|-
|Platinum
|236
|2.3757, 2.0847, 1.8453
|4.2655, 3.7153, 3.1365
|-
|Silver
|237
|0.15943, 0.14512, 0.13547
|3.9291, 3.1900, 2.3808
|}

'''For Texture Artists'''<br />This can be described as the minimal reflection strength of the material. So a low F0 value results in a less intense reflection when looking directly at the material while a higher F0 value causes a stronger and more visible reflection. Unlike traditional specularity however this is not a multiplier for the reflection strength, meaning that reflections at flat angles will always be stronger (called "Fresnel", more information about that [https://www.researchgate.net/figure/Principle-of-the-Fresnel-effect-the-amount-of-reflection-on-a-reflective-surface-depends_fig3_319178578 here] ).

=== Blue Channel ===
* Values from 0 to 64 represent porosity. Examples of the porosity effect can be found [https://github.com/rre36/lab-pbr/wiki/Porosity-Examples here].
** A value of 0 results in the material being 0% porous and a value of 64 in 100% porous.
* Values from 65 to 255 represent subsurface scattering.
** A value of 65 results in 0% scattering and a value of 255 in 100% scattering.
* Both porosity and subsurface scattering are stored linearly.

'''For Texture Artists'''<br/>The porosity value describes how much water a material can absorb. This manifests in the color of the material getting darker and less reflective when wet. This allows for a much more accurate behavior with shader packs supporting both porosity and weather based wetness (e.g. puddles). Below are some example values.
{| class="wikitable"
! Material
! Porosity Value
|-
|Sand
|64
|-
|Wool
|38
|-
|Wood
|12
|-
|Metals and other impermeable materials
|0
|}

=== Alpha Channel ===

* Represents emissive.
** This is stored linearly; A value of 0 will result in 0% light being emitted and a value of 254 in 100% light being emitted.

'''For Texture Artists'''<br/>A value of 255 will not emit light as the value is ignored, this is because an RGB image with no alpha defaults to alpha value of 255.

The color/albedo texture is used to determine the color of the light being emiitted.

=== Reasoning for this Layout ===
For the red and green channels, it is fairly simple:
* Red represents smoothness in most previous formats, and there isn't a good reason to change that.
* Green represents metalness in most previous formats. Assigning values for partial metals, while not realistic, could often be used to somewhat control F0.

For the rest, we only had two channels left for emission, porosity, and subsurface scattering, so two of them had to be stored in the same channel. We chose to have porosity and subsurface scattering in the same channel.

Finally, placing emission in alpha allows the texture artist to easily make the emissive map separate from the rest and then merge it with the specular texture as the alpha channel later, just like the height map in the normal texture.

== Version History ==
'''LabPBR v1.3'''
* F0 is now stored linearly.
* Previously, linear F0 was stored by taking the square root of it and decoded by squaring it.

'''LabPBR v1.2'''
* Changed the way material AO is stored in the normal texture.
* Previously, the material AO was encoded using this method:
** The normal map is brought into the range of [-1, 1] and normalized afterwards.
** The material AO, which is stored in <code>sqrt()</code> in a range of [17, 255] gets multiplied into this.
** Afterwards, the normal map is brought back into the range of [0, 1].
** The AO and normals are decoded like this after bringing the normal map into the range of [-1, 1]:
*** <code>normals = normalize(normalTexture.rgb)</code>
*** <code>ao = length(normalTexture.rgb)</code>

'''LabPBR v1.1'''
* Added specification for hardcoded metals.
* Reorganized emission, subsurface scattering and porosity in order to distribute the available precision based on importance and usage.

'''LabPBR v1.0'''
* Initial specification.


[[Category:LabPBR]]

