#!/usr/bin/env python3
# -*- coding: utf8

'''
ssr2osm.py
Extracts and converts place names from SSR to geojson file with OSM-tagging.
Usage: python3 ssr2osm.py <municipality/county name/Norge> [name type] [-all] [-wfs]
Parameters:
- Municipality/county name or 4/2 digit code, or "Norge"
- Optional: Name type - will generate file with all place names of given name type (use SSR name type code, e.g. "nesISjø"). Combine with "Norge".
- Optional: "-all" will include place names without name=* tags (just loc_name/old_name) and without OSM feature tags (place=*, natural=* etc)
- Optional: "-wfs" will query place names through Kartverket WFS service
'''


import json
import sys
import time
import urllib.request, urllib.parse
import zipfile
from io import BytesIO, TextIOWrapper
from xml.etree import ElementTree as ET
from itertools import chain
import utm


version = "0.5.1"

header = {"User-Agent": "nkamapper/ssr2osm"}

language_codes = {
	# Used in files
	'norsk':        'no',
	'nordsamisk':   'se',
	'lulesamisk':   'smj',
	'sørsamisk':    'sma',
	'skoltesamisk': 'sms',
	'kvensk':       'fkv',
	'engelsk':      'en',
	'svensk':       'sv',
	'russisk':      'ru',

	# Used in WFS
	'nor': 'no',   # Norsk
	'sme': 'se',   # Nordsamisk
	'smj': 'smj',  # Lulesamisk
	'sma': 'sma',  # Sørsamisk
	'sms': 'sms',  # Skoltesamisk
	'fkv': 'fkv',  # Kvensk
	'eng': 'en',   # Engelsk
	'swe': 'sv',   # Svensk
	'rus': 'ru',   # Russisk
	'fin': 'fi',   # Finsk
	'dan': 'da',   # Dansk
	'kal': 'kl',   # Grønlandsk
	'isl': 'is',   # Islandsk
	'deu': 'de',   # Tysk
	'gle': 'ga',   # Irsk
	'fra': 'fr',   # Fransk
	'nld': 'nl',   # Nederlandsk
}

# Not used: The following municipalities will always have language prefix in name:xx=* tagging
# sami_municipalities = ["Kautokeino", "Karasjok", "Porsanger", "Tana", "Nesseby", "Kåfjord",
#						"Lavangen", "Tjeldsund", "Hamarøy", "Hattfjelldal", "Røyrvik", "Snåsa", "Røros"]

include_incomplete_names = False  # True will include unofficial names, i.e. without name=* present, plus names without object tagging

use_wfs = False  # True will load data from WFS rather than from files



def message (output_text):
	'''
	Output message to console.
	'''

	sys.stdout.write (output_text)
	sys.stdout.flush()



def timeformat (sec):
	'''
	Format time.
	'''

	if sec > 3600:
		return "%i:%02i:%02i hours" % (sec / 3600, (sec % 3600) / 60, sec % 60)
	elif sec > 60:
		return "%i:%02i minutes" % (sec / 60, sec % 60)
	else:
		return "%i seconds" % sec



def clean_filename(filename):
	'''
	Convert filename characters to Kartverket standard.
	'''

	return filename.replace("Æ","E").replace("Ø","O").replace("Å","A")\
					.replace("æ","e").replace("ø","o").replace("å","a").replace(" ", "_")



def get_municipality (parameter):
	'''
	Identify municipality name, unless more than one hit
	Returns municipality number, or input parameter if not found.
	'''

	if parameter.isdigit():
		return parameter

	else:
		parameter = parameter
		found_id = ""
		duplicate = False
		for mun_id, mun_name in iter(municipalities.items()):
			if parameter.lower() == mun_name.lower():
				return mun_id
			elif parameter.lower() in mun_name.lower():
				if found_id:
					duplicate = True
				else:
					found_id = mun_id

		if found_id and not duplicate:
			return found_id
		else:
			return parameter



def load_municipalities():
	'''
	Load dict of all municipalities and counties.
	'''

	url = "https://ws.geonorge.no/kommuneinfo/v1/fylkerkommuner?filtrer=fylkesnummer%2Cfylkesnavn%2Ckommuner.kommunenummer%2Ckommuner.kommunenavnNorsk"
	file = urllib.request.urlopen(url)
	data = json.load(file)
	file.close()

	municipalities['00'] = "Norge"
	for county in data:
		municipalities[ county['fylkesnummer'] ] = county['fylkesnavn']
		for municipality in county['kommuner']:
			municipalities[ municipality['kommunenummer'] ] = municipality['kommunenavnNorsk']



def load_tagging():
	'''
	Load OSM tagging for all name types.
	Note: Some will be empty.
	'''

	url = "https://raw.githubusercontent.com/NKAmapper/ssr2osm/main/navnetyper_tagged.json"
	file = urllib.request.urlopen(url)
	data = json.load(file)
	file.close()

	for main_group in data['navnetypeHovedgrupper']:
		for group in main_group['navnetypeGrupper']:
			for name_type in group['navnetyper']:
				tagging[ name_type['navn'] ] = name_type['tags']
				if "fixme" in name_type['tags']:
					tagging[ name_type['navn'] ]['FIXME'] = name_type['tags']['fixme']
					del tagging[ name_type['navn'] ]['fixme']



def parse_coordinates(wkt):
	'''
	Parse WKT string into list of (lon, lat) coordinate tuples.
	Convert from UTM 33N.
	'''

	split_wkt = wkt.split(" ")
	coordinates = []
	for i in range(0, len(split_wkt) - 1, 2):
		x = float(split_wkt[ i ])
		y = float(split_wkt[ i + 1 ])
		if use_wfs:
			node = (x, y)  # 4326
		else:
			lat, lon = utm.UtmToLatLon (x, y, 33, "N")
			node = (lon, lat)
		coordinates.append(node)

	return coordinates



def average_point(coordinates):
	'''
	Compute average of coordinates for area, and mid point for line.
	'''

	if coordinates:
		length = len(coordinates)

		# Average of polygon boundary
		if length > 1 and coordinates[0] == coordinates[-1]:
			avg_lon = sum([node[0] for node in coordinates[:-1]]) / (length - 1)
			avg_lat = sum([node[1] for node in coordinates[:-1]]) / (length - 1)
			return ( avg_lon, avg_lat )

		# Midpoint of line
		else:
			midpoint = length // 2
			if midpoint * 2 == length:
				avg_lon = 0.5 * (coordinates[ midpoint - 1 ][0] + coordinates[ midpoint ][0])
				avg_lat = 0.5 * (coordinates[ midpoint - 1 ][1] + coordinates[ midpoint ][1])
				return ( avg_lon, avg_lat )
			else:
				return coordinates[ midpoint ]
	else:
		return None



def generate_tags(tags, names, language_priority):

	'''
	Generate name tags in correct order and format.
	Result in modified tags dict parameter.
	'''

	main_name = []

	if language_priority is None:
		language_priority = "-".join(names.keys())

	# Convert spellings to name tags.
	# Iterate once per language in language priority order.	

	for language in language_priority.split("-"):
		if language in names:
			for name_tag_type in ['name', 'alt_name', 'loc_name', 'old_name']:
				if names[ language ][ name_tag_type ]:

					name_tag = name_tag_type
					if len(names) > 1 or language not in ["norsk", "nor"]:  # Language suffix for non-Norwegian names
						name_tag += ':' + language_codes[language]
					tags[ name_tag ] = ";".join(names[ language ][ name_tag_type ])

			if names[ language ]['name']:
				main_name.append(";".join(names[ language ]['name']))  # Promote to main name=* tag

	if main_name:
		tags['name'] = " - ".join(main_name)

	# Add OSM tagging

	tags.update( tagging[ tags['TYPE'] ] )

	if "name" in tags and ";" in tags['name']:
		if "FIXME" in tags:
			tags['FIXME'] += ";"
		else:
			tags['FIXME'] = ""
		tags['FIXME'] += "Velg én skrivemåte i name=* og legg resten i alt_name=*"



def process_ssr(municipality_id):
	'''
	Main function of ssr2osm.
	Load municipality or county SSR file and convert to OSM tagging.
	Converted place names are added to "places" list.
	Switch to SSR wfs query function if selected.
	'''

	if use_wfs:
		process_ssr_wfs(municipality_id)
		return

	municipality_name = municipalities[ municipality_id ]

	message ("%s %s\n" % (municipality_id, municipality_name))

	# Load latest SSR file for municipality from Kartverket

	ns_gml = 'http://www.opengis.net/gml/3.2'
	ns_app = 'http://skjema.geonorge.no/SOSI/produktspesifikasjon/StedsnavnForVanligBruk/20181115'

	ns = {
		'gml': ns_gml,
		'app': ns_app
	}

	filename = clean_filename("Basisdata_%s_%s_25833_Stedsnavn_GML" % (municipality_id, municipality_name))
	message ("\tLoading file '%s' ... " % filename)

	request = urllib.request.Request("https://nedlasting.geonorge.no/geonorge/Basisdata/Stedsnavn/GML/" + filename + ".zip", headers=header)
	file_in = urllib.request.urlopen(request)
	zip_file = zipfile.ZipFile(BytesIO(file_in.read()))
	file = zip_file.open(filename + ".gml")

	tree = ET.parse(file)
	file.close()
	file_in.close()
	root = tree.getroot()

	# Loop features, parse, load into data structure and tag

	message ("\n")

	count = 0
	count_hits = 0
	count_language_hits = 0
	points = set()  # Used to discover overlapping points

	if not type_filter:  # Accumulate place names across municipalities/counties if name type filter is used
		places.clear()

	for feature in root:
		
		if "featureMember" not in feature.tag:
			continue

		count += 1
		place_type = feature[0].find("app:navneobjekttype", ns).text

		if type_filter and place_type != type_filter:  # Skip if name filter is used and does not match
			continue

#		place_date = (feature[0].find("app:oppdateringsdato", ns).text)[:10]  # Not used
		place_maingroup = feature[0].find("app:navneobjekthovedgruppe", ns).text
		place_group = feature[0].find("app:navneobjektgruppe", ns).text
#		place_importance = feature[0].find("app:sortering", ns)[0][0].text  # Not used
		place_language_priority = feature[0].find("app:språkprioritering", ns).text
		place_id = feature[0].find("app:stedsnummer", ns).text
		place_municipality = feature[0].find("app:kommune/app:Kommune/app:kommunenummer", ns).text 

		tags = {
			'ssr:stedsnr': place_id,
			'TYPE': place_type,
			'GRUPPE': place_group,
			'HOVEDGRUPPE': place_maingroup
#			'DATO': place_date,
#			'VIKTIGHET': place_importance[-1]
		}

		if len(municipality_id) == 2:
			tags['KOMMUNE'] = "#" + place_municipality + " " + municipalities[ place_municipality ]

		# Get coordinate

		if feature[0].find("app:multipunkt", ns):
			place_coordinate = parse_coordinates(feature[0].find("app:multipunkt", ns)[0][0][0][0].text)[0]  # Use 1st point

		elif feature[0].find("app:posisjon", ns):
			place_coordinate = parse_coordinates(feature[0].find("app:posisjon", ns)[0][0].text)[0]

		elif feature[0].find("app:senterlinje", ns):
			place_coordinate = average_point(parse_coordinates(feature[0].find("app:senterlinje", ns)[0][0].text))

		elif feature[0].find("app:område", ns):
			place_coordinate = average_point(parse_coordinates(feature[0].find("app:område", ns)[0][0][0][0][0][0].text))

		else:
			place_coordinate = (0,0, 0.0)

		# Adjust coordinate slightly to avoid exact overlap (JOSM will merge overlapping nodes)

		place_coordinate = ( round(place_coordinate[0], 7), round(place_coordinate[1], 7) )
		while place_coordinate in points:
			place_coordinate = ( place_coordinate[0], place_coordinate[1] + 0.0001)
		points.add(place_coordinate)

		# Get all spellings/languages for the place

		names = {}

		for placename in feature[0].findall("app:stedsnavn", ns):

			public_placename =  (placename[0].find("app:offentligBruk", ns).text == "true")
#			case_status = placename[0].find("app:navnesakstatus", ns).text  # Not used
			name_status = placename[0].find("app:navnestatus", ns).text
			language = placename[0].find("app:språk", ns).text

			if language not in names:
				names[ language ] = {
					'name': [],
					'alt_name': [],
					'loc_name': [],
					'old_name': []
				}

			for spelling in chain( placename[0].findall("app:skrivemåte", ns), placename[0].findall("app:annenSkrivemåte", ns) ):

				spelling_name = " ".join((spelling[0].find("app:komplettskrivemåte", ns).text).split())  # Fix spaces
				spelling_status = spelling[0].find("app:skrivemåtestatus", ns).text
				priority_spelling = ("skrivemåte" in spelling.tag)

				if name_status == "historisk" or spelling_status == "historisk":
					names[ language ]['old_name'].append(spelling_name)
				elif spelling_status in ['foreslått', 'uvurdert']:
					names[ language ]['loc_name'].append(spelling_name)
				elif public_placename and name_status != "undernavn" and priority_spelling:
					names[ language ]['name'].append(spelling_name)
				else:
					names[ language ]['alt_name'].append(spelling_name)

		# Get name tags and OSM feature tags

		generate_tags(tags, names, place_language_priority)

		# Wrap up and store in places dict

		if "name" in tags and tagging[ place_type ] or include_incomplete_names:

			new_feature = {
				'type': 'Feature',
				'geometry': {
					'type': 'Point',
					'coordinates': place_coordinate
				},
				'properties': tags			
			}

			places.append(new_feature)

			count_hits += 1
			if len(names) > 1 or "norsk" not in names:
				count_language_hits += 1
	
	message ("\tConverted %i of %i place names" % (count_hits, count))
	if count_language_hits > 0:
		message (", including %i non-Norwegian names" % count_language_hits)
	message("\n")



def process_ssr_wfs(municipality_id):
	'''
	Main function of ssr2osm.
	Query SSR by municipality, county or name type, and convert to OSM tagging.
	Converted place names are added to "places" list.
	'''

	municipality_name = municipalities[ municipality_id ]

	message ("%s %s\n" % (municipality_id, municipality_name))

	# Query SSR wfs from Kartverket

	ns_wfs = "http://www.opengis.net/wfs/2.0" 
	ns_gml = 'http://www.opengis.net/gml/3.2'
	ns_app = 'http://skjema.geonorge.no/SOSI/produktspesifikasjon/Stedsnavn/5.0'

	ns = {
		'wfs': ns_wfs,
		'gml': ns_gml,
		'app': ns_app
	}

	if type_filter:
		filter_parameter = ("app:navneobjekttype", type_filter)
	elif len(municipality_id) == 4: 
		filter_parameter = ("app:kommune/app:Kommune/app:kommunenummer", municipality_id)
	else:
		filter_parameter = ("app:kommune/app:Kommune/app:fylkesnummer", municipality_id)

	wfs_filter = '<Filter><PropertyIsEqualTo><ValueReference xmlns:app="%s">%s</ValueReference>' % (ns_app, filter_parameter[0]) + \
					'<Literal>%s</Literal></PropertyIsEqualTo></Filter>' % filter_parameter[1]

	url = "http://wfs.geonorge.no/skwms1/wfs.stedsnavn50?" + \
			"VERSION=2.0.0&SERVICE=WFS&srsName=EPSG:4326&REQUEST=GetFeature&TYPENAME=Sted&resultType=results&Filter="

	header["Content-Type"] = "text/xml"

	message ("\tLoading wfs for '%s' ... " % filter_parameter[1]) # % (url + wfs_filter))

	request = urllib.request.Request(url + urllib.parse.quote(wfs_filter), headers=header)
	file = urllib.request.urlopen(request)

	tree = ET.parse(file)
	file.close()
	root = tree.getroot()

	# Loop features, parse, load into data structure and tag

	message ("\n")

	count = 0
	count_hits = 0
	count_language_hits = 0
	points = set()  # Used to discover overlapping points
	places.clear()

	for feature in root:

		count += 1
		place_status = feature[0].find("app:stedstatus", ns).text
		place_type = feature[0].find("app:navneobjekttype", ns).text
		place_municipality = feature[0].find("app:kommune/app:Kommune/app:kommunenummer", ns).text

		# Skip place under certain condidations
		if place_status not in ["aktiv", "relikt"] or \
				place_municipality[:len(municipality_id)] != municipality_id and municipality_id != "00" or \
				type_filter and place_type != type_filter:
			continue

#		place_date = (feature[0].find("app:oppdateringsdato", ns).text)[:10]  # Not used
		place_maingroup = feature[0].find("app:navneobjekthovedgruppe", ns).text
		place_group = feature[0].find("app:navneobjektgruppe", ns).text
#		place_importance = feature[0].find("app:sortering", ns)[0][0].text  # Not used
		place_id = feature[0].find("app:stedsnummer", ns).text

		place_language_priority = feature[0].find("app:språkprioritering", ns)  # Not used at Svalbard
		if place_language_priority is not None:
			place_language_priority = place_language_priority.text

		tags = {
			'ssr:stedsnr': place_id,
			'TYPE': place_type,
			'GRUPPE': place_group,
			'HOVEDGRUPPE': place_maingroup
#			'DATO': place_date,
#			'VIKTIGHET': place_importance[-1]
		}

		if len(municipality_id) == 2:
			tags['KOMMUNE'] = "#" + place_municipality + " " + municipalities[ place_municipality ]

		# Get coordinate

		geometry = feature[0].find("app:posisjon", ns)
		if geometry.find("gml:MultiPoint", ns):
			place_coordinate = parse_coordinates(geometry[0][0][0][0].text)[0]  # Use 1st point

		elif geometry.find("gml:Point", ns):
			place_coordinate = parse_coordinates(geometry[0][0].text)[0]

		elif geometry.find("gml:LineString", ns):
			place_coordinate = average_point(parse_coordinates(geometry[0][0].text))

		elif geometry.find("gml:MultiCurve", ns):
			place_coordinate = average_point(parse_coordinates(geometry[0][0][0][0].text))

		elif geometry.find("gml:Polygon", ns):
			place_coordinate = average_point(parse_coordinates(geometry[0][0][0][0].text))  # Exterior area only

		else:
			place_coordinate = (0,0, 0.0)

		# Adjust coordinate slightly to avoid exact overlap (JOSM will merge overlapping nodes)

		place_coordinate = ( round(place_coordinate[0], 7), round(place_coordinate[1], 7) )
		while place_coordinate in points:
			place_coordinate = ( place_coordinate[0], place_coordinate[1] + 0.0001)
		points.add(place_coordinate)

		# Get all spellings/languages for the place

		names = {}

		for placename in feature[0].findall("app:stedsnavn", ns):

#			case_status = placename[0].find("app:navnesakstatus", ns).text  # Not used
			name_status = placename[0].find("app:navnestatus", ns).text
			language = placename[0].find("app:språk", ns).text

			if name_status in ["feilført", "avslåttNavnevalg"]:
				continue

			if language not in names:
				names[ language ] = {
					'name': [],
					'alt_name': [],
					'loc_name': [],
					'old_name': []
				}

			for spelling in placename[0].findall("app:skrivemåte", ns):

				spelling_name = " ".join((spelling[0].find("app:langnavn", ns).text).split())  # Fix spaces
				spelling_status = spelling[0].find("app:skrivemåtestatus", ns).text
				priority_spelling = (spelling[0].find("app:prioritertSkrivemåte", ns).text == "true")

				if spelling_status not in ["avslått", "avslåttNavneledd", "feilført"]:

					if name_status == "historisk" or spelling_status == "historisk":
						names[ language ]['old_name'].append(spelling_name)
					elif spelling_status in ['foreslått', 'uvurdert']:
						names[ language ]['loc_name'].append(spelling_name)
					elif name_status != "undernavn" and (priority_spelling or spelling_status == "vedtatt"):
						names[ language ]['name'].append(spelling_name)
					else:
						names[ language ]['alt_name'].append(spelling_name)

		# Get name tags and OSM feature tags

		generate_tags(tags, names, place_language_priority)

		# Wrap up and store in places dict

		if "name" in tags and tagging[ place_type ] or include_incomplete_names:

			new_feature = {
				'type': 'Feature',
				'geometry': {
					'type': 'Point',
					'coordinates': place_coordinate
				},
				'properties': tags			
			}

			places.append(new_feature)

			count_hits += 1
			if len(names) > 1 or "nor" not in names:
				count_language_hits += 1
	
	message ("\tConverted %i of %i place names" % (count_hits, count))
	if count_language_hits > 0:
		message (", including %i non-Norwegian names" % count_language_hits)
	message("\n")



def output_geojson(municipality_id):
	'''
	Save places dict to geosjon file.
	'''

	if len(places) > 0:
		filename = "stedsnavn_%s_%s" % (municipality_id, municipalities[municipality_id].replace(" ", "_"))
		if type_filter:
			filename += "_" + type_filter
		if use_wfs:
			filename += "_wfs"
		if include_incomplete_names:
			filename += "_all"
		filename += ".geojson"

		message ("\tSave to '%s' file ... " % filename)

		geojson_features = { 
			'type': 'FeatureCollection',
			'features': places
		}

		file = open(filename, "w")
		json.dump(geojson_features, file, indent=2, ensure_ascii=False)
		file.close()

		message ("%i place names saved\n\n" % len(places))

	else:
		message ("No place names found, no file saved\n\n")



# Main program

if __name__ == '__main__':

	start_time = time.time()
	message ("\n*** ssr2osm %s ***\n\n" % version)

	places = []          # Will contain converted place names
	tagging = {}         # OSM tagging for each name type
	municipalities = {}  # Codes/names of all counties and municipalities

	# Get parameters

	if "-wfs" in sys.argv:
		use_wfs = True
		municipalities['2100'] = "Svalbard"

	if len(sys.argv) < 2:
		message ("Please provide municipality number or name\n\n")
		sys.exit()

	load_municipalities()
	municipality_id = get_municipality(sys.argv[1])
	if municipality_id is None or municipality_id not in municipalities:
		sys.exit("Municipality or county '%s' not found\n" % sys.argv[1])
	municipality_name = municipalities[ municipality_id ]

	load_tagging()

	type_filter = None
	if len(sys.argv) > 2 and "-" not in sys.argv[2]:
		type_filter = sys.argv[2]
		if type_filter in tagging:
			message("Extracting name type '%s' for %s\n\n" % (type_filter, municipality_name))
		else:
			sys.exit("Name type '%s' not found\n" % type_filter)

	if "-all" in sys.argv or "-alt" in sys.argv:
		include_incomplete_names = True  # Also include place names without main name=* or without OSM feature tagging

	# Execute conversion

	if municipality_name == "Norge":

		if type_filter:
			if use_wfs:
				# Query specifically for name type
				process_ssr("00")
				output_geojson("00")  # Norge				

			else:
				# Process all counties before output
				for municipality_id in sorted(list(municipalities.keys())):
					if len(municipality_id) == 2 and municipality_id != "00":
						process_ssr(municipality_id)

				message ("Compiling Norway file for name type '%s'\n" % type_filter)
				output_geojson("00")  # Norge

		else:
			# Output all municipalities separately
			for municipality_id in sorted(list(municipalities.keys())):
				if len(municipality_id) == 4:
					lap_time = time.time()
					process_ssr(municipality_id)
					output_geojson(municipality_id)
					if use_wfs:
						used_time = time.time() - lap_time
						message("\tDone in %s\n" % timeformat(used_time))

	else:
		# Output one municipality or county
		process_ssr(municipality_id)
		output_geojson(municipality_id)

	used_time = time.time() - start_time
	message("Done in %s\n\n" % timeformat(used_time))
