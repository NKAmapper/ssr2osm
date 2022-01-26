# ssr2osm
Extract and convert place names from Kartverket SSR to OpenStreetMap.

### Usage

<code>python3 ssr2osm.py \<municipality\> [\<name type\>] [-all] [-wfs]</code>

Parameters:
  * _Municipality_: Name of municipality or county to extract. "Norway" will convert all municipalities.
  * _Name type_: Name type to extract, for example _"turisthytte"_, _"serveringssted"_, _"kirke"_, _"nesISjø"_ etc. Combine with "Norway" to extract all occurrences of the name type for the whole country, or combine with municipality or county name. Case sensitive paramter. Plese see [navnetyper_tagged.json](https://github.com/NKAmapper/ssr2osm/blob/main/navnetyper_tagged.json) for list of available name types.
  * <code>-all</code>: Also include place names without the main _name=*_ tag, for example with only _loc_name=*_ or _old_name=*_, or with no OSM feature tagging.
  * <code>-wfs</code>: Query WFS service instead of loading predefined files. Quicker for modest name type queries, but considerably slower for municipalities.

Examples:
 * Vestre Toten municipality: <code>python3 ssr2osm.py "Vestre Toten"</code>
 * Kautokeino municipality, including places without full tagging: <code>python3 ssr2osm.py Kautokeino -all</code>
 * Trøndelag county: <code>python3 ssr2osm.py Trøndelag</code>
 * All municipalities in Norway (in separate files): <code>python3 ssr2osm.py Norge</code>
 * Historic settlements in Innlandet: <code>python3 ssr2osm.py Innlandet historiskBosetting</code>
 * Churches in Norway, from WFS: <code>python3 ssr2osm.py Norge kirke -wfs</code>
 
### References
  
* [Guide: Import av stedsnavn fra SSR2](https://wiki.openstreetmap.org/wiki/No:Import_av_stedsnavn_fra_SSR2)
* [Import progress](https://osmno.github.io/progress-visualizer/?project=ssr)
* [Tagging table](https://drive.google.com/file/d/1krf8NESSyyObpcV8TPUHInUCYiepZ6-m/view)
* [OSM formal import plan](https://wiki.openstreetmap.org/wiki/Import/Catalogue/Central_place_name_register_import_(Norway))
* [Kartverket product specification](https://register.geonorge.no/data/documents/Produktspesifikasjoner_stedsnavn-for-vanlig-bruk_v3_produktspesifikasjon-kartverket-stedsnavn-20181115_.pdf)
