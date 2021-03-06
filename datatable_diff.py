'''
Module for comparing data in two DataTables.
The main entry point is the diff method which takes the two tables and the list of headers which specifies the "primary key" for the tables

diff returns a ResultSet instance which contains the difference data by primary key.

ResultSet contains several methods for pruning expected differences and for slicing up the results to help discover patterns in the changes:
	ignoreField prunes all differences for the given field
	checkRemove prunes differences for the given field which match the given predicate
	checkRemove_multiField prunes differences which affect multiple fields (e.g. we expect the change to move some of the value from one field to another so long as the sum is the same)
	changedFields returns the list of fields which reported differences
	filter takes a Result predicate and returns a new ResultSet containing the matching Results
	pick returns a (pseudo-)random Result from the ResultSet
	original{From|To}Rows returns a DataTable containing the rows which had differences
ResultSet also contains a few methods for formatted output:
	repr(rs) returns a summary of the differences (the number of results in the collection)
	str(rs) returns a concise display of the results showing specifically what changed
	rs.printFormatted() prints a fixed-width-formatted tabular display of the results

ResultSet contains a list of Result instances which represents the changes to a single key entry (which may be multiple from/to rows if the "primary key" used doesn't guarantee uniqueness)
	fromRow and toRow represent the entire original row (or rows)
	repr(result) returns a summary of the differences (the number of from and to rows)
	str(result) returns the differences - if there is one fromRow and one toRow returns the collection of fields which changed, otherwise reports the number of from and to rows
	ignoreField, checkRemove and checkRemove_multiField are also available, but should be ignored on the Result object as they are called from the ResultSet when its corresponding methods are called
'''

from collections import defaultdict
from datatable import DataTable
from datatable_util import AttributeDict, sortKey
from functools import total_ordering

@total_ordering
class Result:
	'''
	Result class representing the difference between rows for a given bucket
Contains the key for this bucket (may be used to find the rows in the original files),
	those fields which changed with the from and to values, and the actual from and to rows
	'''
	def __init__(self, key, keyFields, diffFields, fromRow, toRow):
		self.key = key
		self.diffFields = diffFields
		self.fromRow = fromRow
		self.toRow = toRow
		self.__dict__.update(dict(zip(keyFields,  key)))
		self.__data = {}
		if fromRow and toRow and len(fromRow) == 1 and len(toRow) == 1:
			#extract the fields that are different between the two runs
			for i, (f, t) in enumerate(zip(fromRow[0], toRow[0])):
				if f != t:
					self.__data[i] = f, t
	def __eq__(self,  other):
		if isinstance(other,  Result):
			return self.key == other.key and self.__data == other.__data
		if isinstance(other,  tuple):
			return self.key == other
		if isinstance(other,  dict):
			return not any(self.__dict__[k] != other[k] for k in other.keys())
		raise NotImplementedError
	def __lt__(self, other):
		if self == other:
			return 0
		if isinstance(other, Result):
			def it():
				yield self.key, other.key
				for k in set(self.__data.keys()).union(other.__data.keys()):
					yield self.__data.get(k), other.__data.get(k)
			for s, o in it():
				if s != o:
					return tuple(sortKey(i) for i in s) < tuple(sortKey(i) for i in o)
			return False
		if isinstance(other, tuple):
			return self.key < other
	def comparable(self):
		return bool(self.__data)
	def __bool__(self):
		return bool(self.__data or self.fromRow is None or self.toRow is None or len(self.fromRow) != len(self.toRow))
	def __getitem__(self, field):
		return self.__data[self.diffFields[field]]
	def __contains__(self, field):
		return self.diffFields[field] in self.__data
	def __delitem__(self, field):
		del self.__data[self.diffFields[field]]
	def ignoreField(self, field):
		if field in self:
			del self.__data[self.diffFields[field]]
	def checkRemove(self, field, filterMethod):
		'''
		remove the field from the result if filterMethod returns true for the fromRow, toRow pairs
field is the field to check
filterMethod is a method which takes two parameters (the fromRow and toRow versions of the field) and returns if they can be removed from the result
		'''
		fieldIdx = self.diffFields[field]
		if fieldIdx in self.__data:
			f, t = self.__data[fieldIdx]
			if filterMethod(f, t):
				del self.__data[fieldIdx]
	def checkRemove_multiField(self, filterMethod, *fields):
		'''
		remove the set of fields from the result if filterMethod returns true for those entries
filterMethod is a method which takes two dicts: fromRow and toRow, with those fields specified by the fields parameter and returns if those values can be removed from the result
fields is a list of fields to check and possibly remove
		'''
		fieldIdxs = tuple((field, self.diffFields[field]) for field in fields)
		if any(fieldIdx not in self.__data for field, fieldIdx in fieldIdxs):
			return
		fromRow, toRow = (AttributeDict((field, self.__data[fieldIdx][i]) for field, fieldIdx in fieldIdxs) for i in (0, 1))
		if filterMethod(fromRow, toRow):
			for field, fieldIdx in fieldIdxs:
				del self.__data[fieldIdx]
	def customCheck(self, keyFields, filterMethod, *fieldsToRemove):
		'''
		remove the set of fields from result if filterMethod returns true for the original fromRow and toRow pair
filterMethod is a method which takes two dicts: fromRow and toRow, with the data from the original from and to rows
fields is the list of fields to remove when filterMethod returns true
		'''
		if not (self.fromRow and self.toRow and len(self.fromRow) == 1 and len(self.toRow) == 1):
			return
		fromRow = self.originalFromRows(keyFields)[0]
		toRow = self.originalToRows(keyFields)[0]
		if filterMethod(fromRow, toRow):
			for fieldIdx in tuple(self.diffFields[field] for field in fieldsToRemove):
				if fieldIdx in self.__data:
					del self.__data[fieldIdx]
	def __repr__(self):
		return 'Result(%s) # from rows: %d, to rows: %d' % (repr(self.key), len(self.fromRow) if self.fromRow else 0, len(self.toRow) if self.toRow else 0)
	def __str__(self):
		if self.__data:
			return '%s\t\t%s' % (self.key, {field: self.__data[fieldIdx] for field, fieldIdx in self.diffFields.items() if fieldIdx in self.__data})
		return '%s\tFrom: %s\tTo: %s' % (self.key, len(self.fromRow) if self.fromRow else 0, len(self.toRow) if self.toRow else 0)
	def dataKeys(self):
		return tuple(field for field, fieldIdx in self.diffFields.items() if fieldIdx in self.__data)
	def getLengths(self):
		return [len('%s' % k) for k in self.key]
	def formatKeys(self, lengths):
		return ', '.join(('% ' + str(l) + 's') % k for l, k in zip(lengths, self.key)) + ' |'
	def originalFromRows(self, keyFields):
		return [
			AttributeDict(
				{field: fromRow[fieldIdx]
					for field, fieldIdx in self.diffFields.items()
				}
			) + dict(zip(keyFields, self.key)) for fromRow in (self.fromRow or [])
		]
	def originalToRows(self, keyFields):
		return [
			AttributeDict(
				{field: toRow[fieldIdx]
					for field, fieldIdx in self.diffFields.items()
				}
			) + dict(zip(keyFields, self.key)) for toRow in (self.toRow or [])
		]

class ResultSet:
	'''
	ResultSet class representing the complete set of diff results.
Each bucket in either table is represented by a Result instance.
Provides filtering, iterating over the results and pretty-printing.
	'''
	def __init__(self, keyFields):
		self.__data = defaultdict(lambda : [])
		self.keyFields = keyFields
	def __iadd__(self,  result):
		if isinstance(result,  Result) and result:
			self.__data[result.key].append(result)
		return self
	def filter(self,  criteria):
		newResults = ResultSet(self.keyFields)
		for result in self:
			if criteria(result):
				newResults += result
		return newResults
	def __len__(self):
		return len(self.__data)
	def __iter__(self):
		for rList in self.__data.values():
			yield from rList
	def __getitem__(self,  key):
		if key in self.__data:
			return self.__data[key]
		raise KeyError(key)
	def __delitem__(self,  key):
		if isinstance(key,  Result):
			self.__data[key.key].remove(key)
			if not self.__data[key.key]:
				del self.__data[key.key]
		else:
			del self.__data[key]
	def __repr__(self):
		return 'ResultSet() # length: %d' % len(self.__data)
	def __str__(self):
		def tempIter():
			yield 'Results:'
			for result in self:
				yield str(result)
		return '\n'.join(tempIter())
	def printFormatted(self):
		for line in _formatResults(self):
			print(line)
	def maxKeyLengths(self):
		candidates = [self.keyFields] + [result.key for result in self]
		return [max(len('%s' % row[i]) for row in candidates) for i in range(len(self.keyFields))]
	def formatKeyFields(self, lengths):
		return ', '.join(('% ' + str(l) + 's') % k for l, k in zip(lengths, self.keyFields)) + ' |'
	def pick(self):
		'''Returns a (somewhat) random result object'''
		return next(iter(self.__data.values()))[0]
	def ignoreField(self, field):
		for result in list(self):
			result.ignoreField(field)
			if not result:
				del self[result]
	def changedFields(self):
		'''return the list of fields which changed'''
		return sorted({h for result in self for h in result.dataKeys()})
	def checkRemove(self, field, filterMethod):
		'''
		remove the field from each result if filterMethod returns true for the fromRow, toRow pairs.  Removes any result which has no more inline differences
field is the field to check
filterMethod is a method which takes two parameters (the fromRow and toRow versions of the field) and returns if they can be removed from the result
		'''
		for result in list(self):
			result.checkRemove(field, filterMethod)
			if not result:
				del self[result]
	def checkRemove_multiField(self, filterMethod, *fields):
		'''
		remove the set of fields from each result if filterMethod returns true for those entries.  Removes any result which has no more inline differences
filterMethod is a method which takes two dicts: fromRow and toRow, with those fields specified by the fields parameter and returns if those values can be removed from the result
fields is a list of fields to check and possibly remove
		'''
		for result in list(self):
			result.checkRemove_multiField(filterMethod, *fields)
			if not result:
				del self[result]
	def customCheck(self, filterMethod, *fieldsToRemove):
		'''
		remove the set of fields from each result if filterMethod returns true for the original fromRow and toRow pair.  Removes any result with no more inline differences
filterMethod is a method which takes two dicts: fromRow and toRow, with the data from the original from and to rows
fields is the list of fields to remove when filterMethod returns true
		'''
		for result in list(self):
			result.customCheck(self.keyFields, filterMethod, *fieldsToRemove)
			if not result:
				del self[result]
	def originalFromRows(self):
		'''return the original rows being diffed from'''
		return DataTable(fromRow for result in self for fromRow in result.originalFromRows(self.keyFields))
	def originalToRows(self):
		'''return the original rows being diffed to'''
		return DataTable(toRow for result in self for toRow in result.originalToRows(self.keyFields))

def _bucket(table, bucketHeaders, diffHeaders):
	buckets = defaultdict(lambda : [])
	for row in table:
		key = tuple(row[h] for h in bucketHeaders)
		value = tuple((row[h] if h in row else None) for h in diffHeaders)
		buckets[key].append(value)
	return buckets

def sortRowKey(row):
	return tuple(sortKey(v) for v in row)
	
def diff(fromTable, toTable, *buckets):
	'''The base diff method - buckets the data and ships it off to the Result and ResultSet classes to check for in-line differences'''
	#split the data into buckets
	fromBucketHeaders, toBucketHeaders = ([b for b in buckets if b in table.headers()] for table in (fromTable, toTable))
	commonOtherHeaders = list(set(fromTable.headers()).intersection(toTable.headers()).difference(buckets))
	fromOtherHeaders, toOtherHeaders = ([h for h in table.headers() if h not in bucketHeaders and h not in commonOtherHeaders] for table, bucketHeaders in ((fromTable, fromBucketHeaders), (toTable, toBucketHeaders)))

	diffHeaders = {h: i for i, h in enumerate(commonOtherHeaders + fromOtherHeaders + toOtherHeaders)}
	diffHeadersList = [None] * len(diffHeaders)

	for h, i in diffHeaders.items():
		diffHeadersList[i] = h

	fromBuckets, toBuckets = (_bucket(table, bucketHeaders, diffHeadersList) for table, bucketHeaders in ((fromTable, fromBucketHeaders), (toTable, toBucketHeaders)))
	allKeys = set(fromBuckets.keys()).union(toBuckets.keys())

	results = ResultSet(buckets)
	for key in allKeys:
		if key in fromBuckets:
			fromBucket = sorted(fromBuckets[key], key=sortRowKey)
		else:
			fromBucket = None
		if key in toBuckets:
			toBucket = sorted(toBuckets[key], key=sortRowKey)
		else:
			toBucket = None
		if fromBucket and toBucket and len(fromBucket) == len(toBucket):
			for fromRow, toRow in zip(fromBucket, toBucket):
				results += Result(key, buckets, diffHeaders, [fromRow], [toRow])
		else:
			results += Result(key, buckets, diffHeaders, fromBucket, toBucket)

	return results

def _formatResults(results):
	'''Produce a pretty string for printing to the screen
	format:
header line: "bucket", Field, , Field, ...
data lines:   bucket, field_from, field_to, field_from, field_to...
	'''
	if not results:
		yield 'No results to compare'
		return
	mismatch = sorted(result for result in results if result.fromRow is None or result.toRow is None or len(result.fromRow) != len(result.toRow))

	keyMaxLengths = results.maxKeyLengths()
	keyTotalSize = len(results.formatKeyFields(keyMaxLengths))
	if mismatch:
		yield "Buckets don't match number of rows:"
		yield results.formatKeyFields(keyMaxLengths) + ' From Rows    To Rows'
		for result in mismatch:
			yield result.formatKeys(keyMaxLengths) + ' %-12d %-12d' % (len(result.fromRow) if result.fromRow else 0, len(result.toRow) if result.toRow else 0)
	results = results.filter(lambda result: result.fromRow and result.toRow and len(result.fromRow) == len(result.toRow))
	if not results:
		yield 'No inline differences'
		return
	yield 'Changes in common buckets:'
	headers = results.changedFields()
	resultList = []
	maxLens = [keyTotalSize] + [0]*(len(headers)*2)
	for i in range(len(headers)):
		maxLens[i*2+1] = len(str(headers[i]))
	for result in results:
		buckets = (result.formatKeys(keyMaxLengths),)
		for i, h in enumerate(headers):
			if h in result:
				maxLens[i*2+1] = max(maxLens[i*2+1], len(str(result[h][0])))
				maxLens[i*2+2] = max(maxLens[i*2+2], len(str(result[h][1])))
				buckets += result[h]
			else:
				buckets += '', ''
		resultList.append(buckets)
	maxLens = [str(m+1) for m in maxLens]
	linePattern = '%-' + 's%-'.join(maxLens) + 's'
	yield linePattern % ((results.formatKeyFields(keyMaxLengths),) + sum(((h,'') for h in headers), ()))
	for result in resultList:
		yield linePattern % result

def formatResults(results):
	return '\n'.join(_formatResults(results))

def expectedChange(beforeValue, afterValue):
	return lambda f, t: (f, t) == (beforeValue, afterValue)

def fromNothingToNothing(f, t):
	return not (f or t)
