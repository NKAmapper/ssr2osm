#!/usr/bin/env python3
# -*- coding: utf8


'''
ssr_diff.py
Creates diff between geojson file and Obitius file for place names.
'''

import json
import sys
import urllib.request
from xml.etree import ElementTree as ET


version = "0.2.0"

header = {"User-Agent": "nkamapper/ssr2osm"}



def clean_filename(filename):
	'''
	Convert filename to Kartverket standard spelling.
	'''

	return filename.replace("Æ","E").replace("Ø","O").replace("Å","A")\
					.replace("æ","e").replace("ø","o").replace("å","a").replace(" ", "_")



def get_municipality (parameter):
	'''
	Identify municipality name, unless more than one hit.
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
	Load dict of all municipalities.
	'''

	url = "https://ws.geonorge.no/kommuneinfo/v1/fylkerkommuner?filtrer=fylkesnummer%2Cfylkesnavn%2Ckommuner.kommunenummer%2Ckommuner.kommunenavnNorsk"
	file = urllib.request.urlopen(url)
	data = json.load(file)
	file.close()

	for county in data:
		for municipality in county['kommuner']:
			municipalities[ municipality['kommunenummer'] ] = municipality['kommunenavnNorsk']



def get_names(tags):
	'''
	Extract name tags with sorted content, to avoid differeces due to ordering.
	'''

	names = {}
	for key, value in iter(tags.items()):
		if "name" in key:
			value_split = value.split(" - ")
			for i in range(len(value_split)):
				value_split[i] = ";".join(sorted(value_split[i].split(";")))
			names[key] = " - ".join(value_split)
	return names



# Main program

if __name__ == '__main__':

	print("\nDiff between SSR geosjon and Obtitus files\n")

	# Get municipality

	municipalities = {}
	load_municipalities()

	municipality_id = get_municipality(sys.argv[1])
	if municipality_id is None or municipality_id not in municipalities:
		sys.exit("Municipality '%s' not found\n" % sys.argv[1])
	municipality_name = municipalities[ municipality_id ]
	print ("Municipality: %s %s" % (municipality_id, municipality_name))

	# Load geosjon file (file 1)

	filename = "stedsnavn_%s_%s.geojson" % (municipality_id, municipality_name)
	file = open(filename)
	places1 = json.load(file)
	file.close()
	print ("File 1: %s ... %i place names" % (filename, len(places1['features'])))

	places2 = {}

	# Load Obtitus file (file 2)

	url = "https://obtitus.github.io/ssr2_to_osm_data/data/%s/%s.osm" % (municipality_id, municipality_id)
	request = urllib.request.Request(url, headers=header)
	file = urllib.request.urlopen(request)

	tree = ET.parse(file)
	file.close()
	root = tree.getroot()

	for node in root.iter('node'):
		entry = {
			'coordinate': (float(node.get('lon')), float(node.get('lat'))),
			'tags': {}
		}	
		for tag in node.iter('tag'):
			if "name" in tag.get('k') or tag.get('k') in ["ssr:stedsnr", "ssr:type"]:
				entry['tags'][ tag.get('k') ] = tag.get('v').replace("  ", " ")

		places2[ entry['tags']['ssr:stedsnr'] ] = entry

	print ("File 2: %s ... %i place names" % (url, len(places2)))

	# Compare files and output diff

	print ("\n\nDiff between files 1 and 2:\n")

	places1_not_found = []  # Will contain non-matched places from file 1

	for place1 in places1['features'][:]:
		place_id = place1['properties']['ssr:stedsnr']
		found = False

		if place_id in places2:
			names1 = set(get_names(place1['properties']).items())
			names2 = set(get_names(places2[ place_id ]['tags']).items())
			if names2 - names1:
				print ("%s: Missing tags in file 1: %s" % (place_id, dict(sorted(names2 - names1))))
				found = True
			if names1 - names2:
				print ("%s: Missing tags in file 2: %s" % (place_id, dict(sorted(names1 - names2))))
				found = True

			del places2[ place_id ]  # Places2 will only contain non-matched places from file 2

		else:
			places1_not_found.append(place1)

		if found:
			print ("")

	# Output places not found

	if places2:
		print ("\nPlaces from file 2 not found in file 1:\n")
		for place2_id, place2 in iter(places2.items()):
			print ("%s: Place not found in file 1. %s" % (place2_id, get_names(place2['tags'])))

	if places1_not_found:
		print ("\nPlaces from file 1 not found in file 2:\n")
		for place1 in places1_not_found:
			place_id = place1['properties']['ssr:stedsnr']
			print ("%s: Place not found in file 2. %s" % (place_id, get_names(place1['properties'])))

	print("")
