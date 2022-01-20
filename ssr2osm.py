#!/usr/bin/env python3
# -*- coding: utf8

'''
ssr2osm.py
Extracts and converts place names from SSR to geojson file with OSM-tagging.
Usage: python3 ssr2osm.py <municipality/county name> [name type] [-all]
Parameters:
- Municipality/county name or 4/2 digit code, or "Norge" if [name type] parameter is provided
- Optional: Name type - will generate file with all place names of given name type (use SSR name type code, e.g. "nesISjø"). Combine with "Norge".
- Optional: "-all" will include place names without name=* tags (just loc_name/old_name) and without OSM feature tags (place=*, natural=* etc) 
'''


import json
import sys
import time
import urllib.request
import zipfile
from io import BytesIO, TextIOWrapper
from xml.etree import ElementTree as ET
from itertools import chain
import utm


version = "0.3.0"

header = {"User-Agent": "nkamapper/ssr2osm"}

language_codes = {
	'norsk': 'no',
	'nordsamisk': 'se',
	'lulesamisk': 'smj',
	'sørsamisk': 'sma',
	'skoltesamisk': 'sms',
	'kvensk': 'fkv',
	'engelsk': 'en',
	'russisk': 'ru',
	'svensk': 'sv'
}

# The following municipalities will always have language prefix in name:xx=* tagging
multi_language_municipalities = []  # ["Kautokeino", "Karasjok", "Porsanger", "Tana", "Nesseby", "Kåfjord"] 
	# Excluded due to < 15% sami names: ["Lavangen", "Tjeldsund", "Hamarøy", "Hattfjelldal", "Røyrvik", "Snåsa", "Røros"]

include_incomplete_names = False  # True will include unofficial names, i.e. without name=* present, plus names without object tagging



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
		[lat, lon] = utm.UtmToLatLon (x, y, 33, "N")
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



def process_municipality(municipality_id):
	'''
	Main function of ssr2osm.
	Load municipality or county SSR file and convert to OSM tagging.
	Converted place names are added to "places" list.
	'''

	municipality_name = municipalities[ municipality_id ]
	multi_language = (municipality_name in multi_language_municipalities)

	if len(municipality_id) == 2:
		message ("County: ")
	else:
		message ("Municipality: ")
	message ("%s %s\n" % (municipality_id, municipality_name))

	# Load latest SSR file for municipality from Kartverket

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

	ns_gml = 'http://www.opengis.net/gml/3.2'
	ns_app = 'http://skjema.geonorge.no/SOSI/produktspesifikasjon/StedsnavnForVanligBruk/20181115'

	ns = {
		'gml': ns_gml,
		'app': ns_app
	}

	# Loop features, parse, load into data structure and tag

	message ("\n")

	count = 0
	count_hits = 0
	count_language_hits = 0
	points = set()  # Used to discover overlapping points

	if not name_filter:  # Accumulate place names across municipalities/counties if name type filter is used
		places.clear()

	for feature in root:
		if "featureMember" in feature.tag:

			count += 1
#			place_date = (feature[0].find("app:oppdateringsdato", ns).text)[:10]		
			place_maingroup = feature[0].find("app:navneobjekthovedgruppe", ns).text
			place_group = feature[0].find("app:navneobjektgruppe", ns).text
			place_type = feature[0].find("app:navneobjekttype", ns).text
#			place_importance = feature[0].find("app:sortering", ns).text
			place_language_priority = feature[0].find("app:språkprioritering", ns).text
			place_id = feature[0].find("app:stedsnummer", ns).text

			if name_filter and place_type != name_filter:  # Skip if name filter is used and does not match
				continue

			tags = {
				'ssr:stedsnr': place_id,
				'TYPE': place_type,
				'GRUPPE': place_group,
				'HOVEDGRUPPE': place_maingroup
#				'VIKTIGHET': place_importance[-1]
			}

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
#				case_status = placename[0].find("app:navnesakstatus", ns).text
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

					spelling_name = (spelling[0].find("app:komplettskrivemåte", ns).text).replace("  ", " ")
					spelling_status = spelling[0].find("app:skrivemåtestatus", ns).text

					if name_status == "historisk" or spelling_status == "historisk":
						names[ language ]['old_name'].append(spelling_name)
					elif spelling_status in ['foreslått', 'uvurdert']:
						names[ language ]['loc_name'].append(spelling_name)
					elif public_placename and name_status != "undernavn" and "skrivemåte" in spelling.tag:
						names[ language ]['name'].append(spelling_name)
					else:
						names[ language ]['alt_name'].append(spelling_name)

			# Determine name tagging

			main_name = []
			non_norwegian = False

			for language in place_language_priority.split("-"):
				if language in names:
					for name_tag_type in ['name', 'alt_name', 'loc_name', 'old_name']:
						if names[ language ][ name_tag_type ]:

							name_tag = name_tag_type
							if multi_language or len(names) > 1 or language != "norsk":
								name_tag += ':' + language_codes[language]
							tags[ name_tag ] = ";".join(names[ language ][ name_tag_type ])

							non_norwegian = (non_norwegian or language != "norsk")

					if names[ language ]['name']:
						main_name.append(";".join(names[ language ]['name']))

			if main_name:
				tags['name'] = " - ".join(main_name)

			# Wrap up and store in places dict

			if "name" in tags and tagging[ place_type ] or include_incomplete_names:

				tags.update( tagging[ place_type ] )

				if "name" in tags and ";" in tags['name']:
					if "FIXME" in tags:
						tags['FIXME'] += ";"
					else:
						tags['FIXME'] = ""
					tags['FIXME'] += "Velg én skrivemåte i name=* og legg resten i alt_name=*"

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
				if non_norwegian:
					count_language_hits += 1
	
	message ("\tConverted %i of %i place names" % (count_hits, count))
	if count_language_hits > 0:
		message (", including %i non-Norwegian names" % count_language_hits)
	message("\n")



def output_geojson(municipality_id):
	'''
	Save places dict to geosjon file.
	'''

	filename = "stedsnavn_%s_%s" % (municipality_id, municipalities[municipality_id].replace(" ", "_"))
	if name_filter:
		filename += "_" + name_filter
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



# Main program

if __name__ == '__main__':

	start_time = time.time()
	message ("\n*** ssr2osm %s ***\n\n" % version)

	places = []          # Will contain coverted place names
	tagging = {}         # OSM tagging for each name type
	municipalities = {}  # Codes/names of all counties and municipalities

	# Get parameters

	if len(sys.argv) < 2:
		message ("Please provide municipality number or name\n\n")
		sys.exit()

	load_municipalities()
	municipality_id = get_municipality(sys.argv[1])
	if municipality_id is None or municipality_id not in municipalities:
		sys.exit("Municipality '%s' not found\n" % sys.argv[1])
	municipality_name = municipalities[ municipality_id ]

	load_tagging()

	name_filter = None
	if len(sys.argv) > 2 and "-" not in sys.argv[2]:
		name_filter = sys.argv[2]
		if name_filter in tagging:
			message("Extracting name type '%s' for %s\n\n" % (name_filter, municipality_name))
		else:
			sys.exit("Name type '%s' not found\n" % name_filter)

	if "-all" in sys.argv:
		include_incomplete_names = True  # Also include place names without main name=* or without OSM feature tagging

	# Execute conversion

	if municipality_name == "Norge":
		for municipality_id in sorted(list(municipalities.keys())):
			if name_filter:
				if len(municipality_id) == 2 and municipality_id != "00":  # Process all counties before output
					process_municipality(municipality_id)

			elif len(municipality_id) == 4: # Process each municipality
				process_municipality(municipality_id)
				output_geojson(municipality_id)

		if name_filter:
			message ("Compiling Norway file for name type '%s'\n" % name_filter)
			output_geojson("00")  # Norge

	else:
		process_municipality(municipality_id)
		output_geojson(municipality_id)

	used_time = time.time() - start_time
	message("Done in %s\n\n" % timeformat(used_time))


