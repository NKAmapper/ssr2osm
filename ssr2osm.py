#!/usr/bin/env python3
# -*- coding: utf8

'''
ssr2osm.py
Extracts and converts place names from SSR to geojson file with OSM-tagging.
Usage: python3 ssr2osm.py <municipality/county name/Norge> [name type] [-all] [-wfs] [-nobuilding] [-clean]
Parameters:
- Municipality/county name or 4/2 digit code, or "Norge"
- Optional: Name type - will generate file with all place names of given name type (use SSR name type code, e.g. "nesISjø"). Combine with "Norge".
- Optional: "-all" will include place names without name=* tags (just loc_name/old_name) and without OSM feature tags (place=*, natural=* etc)
- Optional: "-wfs" will query place names through Kartverket WFS service
- Optional: "-nobuilding" will skip test for overlap with buildings
- Optional: "-extra" will save extra information tags
'''


import json
import sys
import time
import math
import random
import os.path
import urllib.request, urllib.parse
import zipfile
from io import BytesIO, TextIOWrapper
from xml.etree import ElementTree as ET
from itertools import chain
import utm


version = "1.2.0"

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

building_folder = "~/Jottacloud/osm/bygninger/"  # Folder containing import building files (default folder tried first)

include_incomplete_names = False  # True will include unofficial names, i.e. without name=* present, plus names without object tagging

use_wfs = False  # True will load data from WFS rather than from files

avoid_building = True  # True will relocated place nodes if inside (import) buildings

less_tags = True  # True to avoid extra information tags (SORTERING etc)

duplicate_tolerance = 500  # Max. meter distance for identifying duplicate names

relocate_tolerance = 50  # Max. meter distance for flagging relocated node (away from building)



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



def compute_distance (point1, point2):
	'''
	Compute approximation of distance between two coordinates, (lon,lat), in kilometers.
	Works for short distances.
	'''

	lon1, lat1, lon2, lat2 = map(math.radians, [point1[0], point1[1], point2[0], point2[1]])
	x = (lon2 - lon1) * math.cos( 0.5*(lat2+lat1) )
	y = lat2 - lat1
	return 6371000.0 * math.sqrt( x*x + y*y )  # Metres



def inside_polygon (point, polygon):
	'''
	Tests whether point (x,y) is inside a polygon.
	Ray tracing method.
	'''

	if polygon[0] == polygon[-1]:
		x, y = point
		n = len(polygon)
		inside = False

		p1x, p1y = polygon[0]
		for i in range(n):
			p2x, p2y = polygon[i]
			if y > min(p1y, p2y):
				if y <= max(p1y, p2y):
					if x <= max(p1x, p2x):
						if p1y != p2y:
							xints = (y-p1y) * (p2x-p1x) / (p2y-p1y) + p1x
						if p1x == p2x or x <= xints:
							inside = not inside
			p1x, p1y = p2x, p2y

		return inside

	else:
		return None



def coordinate_offset (node, distance):
	'''
	Calculate new node with given distance offset in meters.
	Works over short distances.
	'''

	m = (1 / ((math.pi / 180.0) * 6378137.0))  # Degrees per meter

	latitude = node[1] + (distance * m)
	longitude = node[0] + (distance * m) / math.cos( math.radians(node[1]) )

	return (longitude, latitude)



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
		for municipality in county['kommuner']:
			municipalities[ municipality['kommunenummer'] ] = municipality['kommunenavnNorsk']
		municipalities[ county['fylkesnummer'] ] = county['fylkesnavn']



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

					# Temporary
					if "peak" in tagging[ name_type['navn'] ]['FIXME']:
						del tagging[ name_type['navn'] ]['FIXME']
	



def load_n50_n100 (municipality_id):
	'''
	Load visibility priority for place names from N50.
	Codes < 100 are from N100. Codes < 122 from N50 are relevant for hamlets.
	'''

	visibility['N50'] = {}
	visibility['N100'] = {}

	for scale in ["N50", "N100"]:

		if scale == "N50" and len(municipality_id) == 2:  # No N50 for counties
			continue

		message ("\tLoading %s data from Kartverket ... " % scale)

		# Load latest N50 file for municipality from Kartverket

		filename = clean_filename("Basisdata_%s_%s_25833_%sKartdata_GML" % (municipality_id, municipalities[municipality_id], scale))
		url = "https://nedlasting.geonorge.no/geonorge/Basisdata/%sKartdata/GML/%s.zip" % (scale, filename)

		request = urllib.request.Request(url, headers=header)
		file_in = urllib.request.urlopen(request)
		zip_file = zipfile.ZipFile(BytesIO(file_in.read()))

		filename2 = filename.replace("Kartdata", "Stedsnavn")
		try:
			file = zip_file.open(filename2 + ".gml")
		except KeyError:
			message ("\t*** %s not found\n" % scale)
			continue

		tree = ET.parse(file)
		file.close()
		file_in.close()
		root = tree.getroot()

		ns_gml = "http://www.opengis.net/gml/3.2"
		if scale == "N50":
			ns_app = "https://skjema.geonorge.no/SOSI/produktspesifikasjon/N50/20230401"
		else:
			ns_app = "https://skjema.geonorge.no/SOSI/produktspesifikasjon/N100/20230401"

		ns = {
			'gml': ns_gml,
			'app': ns_app
		}

		count = 0

		# Loop place names and store visibility code in dict.

		for place in root.findall(".//app:StedsnavnTekst", ns):
			place_id = place.find("app:stedsnummer", ns)
			if place_id is not None:
				place_id = place_id.text
				text_code = place.find("app:tekstformatering/app:Tekstformatering/app:skriftkode", ns).text
				visibility[ scale ][ int(place_id) ] = int(text_code)
				count += 1

		message ("%i places found\n" % count)



def add_fixme(tags, comment):
	'''
	Add fixme comment to tags. Create fixme tag if not present.
	'''

	if "FIXME" in tags:
		if comment not in tags['FIXME']:
			tags['FIXME'] = comment + ";" + tags['FIXME']
	else:
		tags['FIXME'] = comment



def generate_tags(tags, names, language_priority):

	'''
	Generate name tags in correct order and format.
	Result in modified tags dict parameter.
	'''

	main_name = []
	extra_main_name = []

	if language_priority is None:
		language_priority = "-".join(names.keys())

	# Convert spellings to name tags.
	# Iterate once per language in language priority order.	

	for language in language_priority.split("-"):
		if language in names:

			# Ensure only one main name, keep the longest ("Vestre Berg" before "Berg")
			if len(names[ language ]['name']) > 1:
				names[ language ]['name'].sort(key=len, reverse=True)
				extra_main_name.extend(names[ language ]['name'][1:] )
				names[ language ]['alt_name'] = names[ language ]['name'][1:] + names[ language ]['alt_name']
				names[ language ]['name'] = [ names[ language ]['name'][0] ]

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

	# Override place tag if higher visibility from N50 or N100

	int_id = int(tags['ssr:stedsnr'])

	code = None
	if int_id in visibility['N50']:
		code = visibility['N50'][ int_id ]
		tags['N50'] = str(code)

	if int_id in visibility['N100']:
		code = visibility['N100'][ int_id ]
		tags['N100'] = str(code)

	if code and tags['HOVEDGRUPPE'] == "bebyggelse" and "place" in tags:

		# First tests based on place rank from N100

		if code == 1:
			tags['place'] = "city"

		elif code == 3 and tags['place'] != "town":
			tags['FIXME'] = "Sjekk om place=town er brukt (N100)"

		elif code in [4, 5] and tags['place'] not in ["town", "village", "suburb"]:
			tags['FIXME'] = "Sjekk endring fra place=%s (N100)" % tags['place']
			tags['place'] = "village"

		elif code == 6 and tags['place'] != "quarter":
			tags['FIXME'] = "Vurder place=quarter (N100)"		

		# Next tests will try to identify hamlets

		elif tags['place'] in ["farm", "isolated_dwelling"]:

			if int_id in visibility['N100'] and int_id in visibility['N50'] and visibility['N50'][ int_id ] < 122:  # Both N100 and N50
				tags['FIXME'] = "Sjekk endring fra place=%s (N100/N50)" % tags['place']
				tags['place'] = "hamlet"

			elif code < 100:  # N100
				if tags['SORTERING'] == "E":
					tags['FIXME'] = "Sjekk endring fra place=%s (N100)" % tags['place']
					tags['place'] = "hamlet"
				else:
					tags['FIXME'] = "Vurder place=hamlet (N100)"

			elif code < 122:  # N50
				if tags['SORTERING'] == "E":
					tags['FIXME'] = "Sjekk endring fra place=%s (N50)" % tags['place']
					tags['place'] = "hamlet"
				else:
					tags['FIXME'] = "Vurder place=hamlet (N50)"

	# Tag most important peaks

	elif code and tags['GRUPPE'] == "høyder":
		if code < 100 and "natural" in tags and tags['natural'] == "hill":  # Peak is present in N100
			tags['natural'] = "peak"
			if "place" in tags:
				del tags['place']

	# Warn about multiple main names

	if extra_main_name:
		add_fixme(tags, "Sjekk likestilt hovednavn '%s' i alt_name" % ";".join(extra_main_name))

	if less_tags:
		del tags['SORTERING']
#		if "DUPLIKAT" in tags:
#			del tags['DUPLIKAT']



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

	# Load N50/N100 visibility data

	load_n50_n100(municipality_id)

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
	count_extra_main_names = 0
	points = set()  # Used to discover overlapping points

	if not type_filter:  # Accumulate place names across municipalities/counties if name type filter is used
		places.clear()
		placeids.clear()

	for feature in root:
		
		if "featureMember" not in feature.tag:
			continue

		count += 1
		place_type = feature[0].find("app:navneobjekttype", ns).text
		place_id = feature[0].find("app:stedsnummer", ns).text

		if type_filter and place_type != type_filter:  # Skip if name filter is used and does not match
			continue

		if int(place_id) in placeids:  # Skip if duplicate place
			continue

		placeids.add( int(place_id) )

#		place_date = (feature[0].find("app:oppdateringsdato", ns).text)[:10]  # Not used
		place_maingroup = feature[0].find("app:navneobjekthovedgruppe", ns).text
		place_group = feature[0].find("app:navneobjektgruppe", ns).text
		place_sorting = feature[0].find("app:sortering", ns).text  # Not used
		place_municipality = feature[0].find("app:kommune/app:Kommune/app:kommunenummer", ns).text 

		place_language_priority = feature[0].find("app:språkprioritering", ns)
		if place_language_priority is not None:
			place_language_priority = feature[0].find("app:språkprioritering", ns).text

		tags = {
			'ssr:stedsnr': place_id,
			'TYPE': place_type,
			'GRUPPE': place_group,
			'HOVEDGRUPPE': place_maingroup,
#			'DATO': place_date,
			'SORTERING': place_sorting[-1]
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
			place_coordinate = ( place_coordinate[0], place_coordinate[1] + 0.001)
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
			if "FIXME" in tags and "likestilt" in tags['FIXME']:
				count_extra_main_names += 1

	message ("\tConverted %i of %i place names" % (count_hits, count))
	if count_language_hits > 0:
		message (", including %i non-Norwegian names" % count_language_hits)
	message("\n")

	if count_extra_main_names > 0:
		message ("\t%i extra name=* moved to alt_name=*\n" % count_extra_main_names)

	check_duplicates()

	if avoid_building and len(municipality_id) == 4:
		check_building_overlap(municipality_id)



def process_ssr_wfs(municipality_id):
	'''
	Main function of ssr2osm.
	Query SSR by municipality, county or name type, and convert to OSM tagging.
	Converted place names are added to "places" list.
	'''

	municipality_name = municipalities[ municipality_id ]

	message ("%s %s\n" % (municipality_id, municipality_name))

	if municipality_id != "00":  # Municipalities and counties only
		load_n50_n100(municipality_id)

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

	message ("\tLoading wfs for '%s' ... " % filter_parameter[1])

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

		# Skip place under certain conditions
		if place_status not in ["aktiv", "relikt"] or type_filter and place_type != type_filter:
			continue

#		place_date = (feature[0].find("app:oppdateringsdato", ns).text)[:10]  # Not used
		place_maingroup = feature[0].find("app:navneobjekthovedgruppe", ns).text
		place_group = feature[0].find("app:navneobjektgruppe", ns).text
		place_sorting = feature[0].find("app:sortering", ns)[0][0].text
		place_id = feature[0].find("app:stedsnummer", ns).text

		place_municipality = feature[0].find("app:kommune/app:Kommune/app:kommunenummer", ns)
		if place_municipality is not None:
			place_municipality = place_municipality.text

		place_language_priority = feature[0].find("app:språkprioritering", ns)  # Not used at Svalbard
		if place_language_priority is not None:
			place_language_priority = place_language_priority.text

		tags = {
			'ssr:stedsnr': place_id,
			'TYPE': place_type,
			'GRUPPE': place_group,
			'HOVEDGRUPPE': place_maingroup,
#			'DATO': place_date,
			'SORTERING': place_sorting[-1]
		}

		if len(municipality_id) == 2 and place_municipality:
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
		count_extra_main_names = 0

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
			if "FIXME" in tags and "likestilt" in tags['FIXME']:
				count_extra_main_names += 1
	
	check_duplicates()

	if avoid_building and len(municipality_id) == 4:
		check_building_overlap(municipality_id)

	message ("\tConverted %i of %i place names" % (count_hits, count))
	if count_language_hits > 0:
		message (", including %i non-Norwegian names" % count_language_hits)
	message("\n")

	if count_extra_main_names > 0:
		message ("\t%i extra name=* moved to alt_name=*\n" % count_extra_main_names)



def sort_place(place):
	'''
	Generate sort key so that least important places are selected for removal.
	'''
	if place['properties']['place'] in place_order:
		value = place_order.index(place['properties']['place'])
		if "FIXME" in place['properties'] and place['properties']['place'] in ["locality", "isolated_dwelling", "farm"]:
			if  "(N50)" in place['properties']['FIXME']:
				value = place_order.index("neighbourhood") - 0.11
			elif "(N100)" in place['properties']['FIXME']:
				value = place_order.index("neighbourhood") - 0.10
		else:
			if place['properties']['TYPE'] == "navnegard":
				value += 0.2
			elif place['properties']['TYPE'] == "gard":
				value += 0.1
			if "alt_name" in place['properties']:
				value += 0.01
			if "old_name" in place['properties']:
				value += 0.01

		return value
	else:
		message ("\tUnknown place: %s\n" % place['properties']['place'])
		return 0



def check_duplicates():
	'''
	Discover close duplicate names among "bebyggelse" places and tag in fixme.
	'''

	global place_order
	place_order  = ['locality', 'square', 'isolated_dwelling', 'farm', 'neighbourhood', 'hamlet', 'quarter', 'suburb', 'village', 'town', 'city']

	count = 0
	place_names = {}

	# Building dict with duplicate place names

	for place in places:
		name = place['properties']['name']
		if place['properties']['HOVEDGRUPPE'] == "bebyggelse" and "place" in place['properties']:
			if name not in place_names:
				place_names[ name ] = [ place ]
			else:
				place_names[ name ].append(place)

	# Tag least significant duplicates with fixme if close enough.

	for name, duplicates in iter(place_names.items()):
		if len(duplicates) > 1:
			duplicates.sort(key = sort_place)

			while len(duplicates) > 1:
				ref_point = duplicates.pop()['geometry']['coordinates']
				for place in duplicates:
					distance = compute_distance(ref_point, place['geometry']['coordinates'])
					if distance < duplicate_tolerance:
						add_fixme(place['properties'], "Fjern duplikat")
						place['properties']['DUPLIKAT'] = str(int(distance))
						count += 1

	if count > 1:
		message ("\t%i close place name duplicates identified\n" % count)



def check_building_overlap(municipality_id):
	'''
	Discover place node within building and relocated it slightliy.
	'''

	message ("\tChecking building overlap ... ")

	# Load building file for municipality

	filename = "bygninger_%s_%s.geojson" % (municipality_id, municipalities[ municipality_id ].replace(" ", "_"))

	if not os.path.isfile(filename):
		test_filename = os.path.expanduser(building_folder + filename)
		if os.path.isfile(test_filename):
			filename = test_filename
		else:
			message("*** File '%s'not found\n" % filename)
			return

	file = open(filename)
	building_data = json.load(file)
	file.close()

	buildings = [building for building in building_data['features'] if building['geometry']['type'] == "Polygon"]

	# Add polygon bbox to speed up overlap test later

	for building in buildings:
		building['min_bbox'] = (min([ node[0] for node in building['geometry']['coordinates'][0] ]), \
								min([ node[1] for node in building['geometry']['coordinates'][0] ]))
		building['max_bbox'] = (max([ node[0] for node in building['geometry']['coordinates'][0] ]), \
								max([ node[1] for node in building['geometry']['coordinates'][0] ]))

	margin_overlap = 100  # meters
	relocate_step = 2  # meters
	count = 0

	# Iterate all place names and relocate slightly if inside building (only for place=* tags)

	for place in places:
		if place['properties']['HOVEDGRUPPE'] != "bebyggelse" or "place" not in place['properties']:
			continue

		node = place['geometry']['coordinates']
		min_bbox = coordinate_offset(node, - margin_overlap)
		max_bbox = coordinate_offset(node, + margin_overlap) 

		# Identify buildings in vicinity of place name

		target_buildings = []
		for building in buildings:
			if min_bbox[0] < building['max_bbox'][0] and max_bbox[0] > building['min_bbox'][0] and \
					min_bbox[1] < building['max_bbox'][1] and max_bbox[1] > building['min_bbox'][1]:
				target_buildings.append(building)

		# Relocate place name slightly until outside of buildings

		if target_buildings:
			inside = True
			while inside:
				for building in target_buildings:
					inside = inside_polygon(node, building['geometry']['coordinates'][0])
					if inside:
						node = coordinate_offset(building['min_bbox'], - random.uniform(relocate_step, 2 * relocate_step))
						break

			if place['geometry']['coordinates'] != node:
				distance = compute_distance(place['geometry']['coordinates'], node)
				if distance > relocate_tolerance:
					add_fixme(place, "Sjekk plassering (flyttet %im)" % distance)
				place['geometry']['coordinates'] = (round(node[0], 7), round(node[1], 7))
#				place['properties']['FLYTTET'] = str(int(distance))
				count += 1

	message ("%i place nodes relocated away from buildings\n" % count)



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
	placeids = set()     # Will contain all place id's (stedsnr)
	visibility = {       # Place id's (stedsnr) for high visibility in N50 and N100
		'N100': {},
		'N50': {}
	}

	# Get parameters

	if "-wfs" in sys.argv:
		use_wfs = True
		municipalities['2100'] = "Svalbard"

	if len(sys.argv) < 2:
		message ("Please provide municipality number or name\n\n")
		sys.exit()

	if "-nobuilding" in sys.argv:
		avoid_building = False

	if "-extra" in sys.argv:
		less_tags = False

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

					if municipality_id < "":  # Skip if need to restart
						continue

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
