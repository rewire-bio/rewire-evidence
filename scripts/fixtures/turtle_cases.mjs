// Turtle documents for n3, and what is recorded of each. record_schema_fixture.mjs imports
// this and hands it the data repository's own n3, so the Python port of n3's Turtle reader
// (src/rewire_evidence/turtle.py) is held to the parser and not to a reading of it.
//
// Three things are recorded into tests/fixtures/turtle.json:
//   documents   written by hand, one for each thing the lexer and the parser can do or
//               refuse, with every statement n3 reads or the message it throws.
//   mutations   documents damaged by a generator with a stated seed, so the corpus is not
//               only the cases its author thought of.
//   decoding    byte strings as Node decodes them, since a vocabulary is bytes on disk.

const P = "@prefix : <http://e/> .\n";
const S = "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n";

// Each entry is a document. Most are refused, and the refusal is what is recorded.
export const documents = [
  // White space, comments and line counting
  "",
  " \t\n\r\n\r",
  "# only a comment",
  "# a comment\n<http://a/s> <http://a/p> <http://a/o> . # after\n<http://a/s>\t<http://a/p>\r\n\"l\" .# end",
  `${P}:a :b :c .\r\n\r\n:d :e`,
  `${P}:a :b :c .\r:d :e`,
  `${P}:a\u000c:b :c .`,
  `${P}:a\u000b:b :c .`,
  `${P}:a\u00a0:b :c .`,
  `${P}:a\u001c:b :c .`,
  `${P}:a\u0085:b :c .`,
  `\ufeff${P}:a :b :c .`,
  `${P}\ufeff:a :b :c .`,
  `${P}:a :b :c\u2028.`,
  // IRIs
  "<http://a/s> <http://a/p> <http://a/o>.",
  "<http://a/\\u00e9> <http://a/\\U0001F600> <http://a/\\u0041\\U00000042> .",
  "<http://a/\\u0020> <http://a/p> <http://a/o> .",
  "<http://a/\\u007B> <http://a/p> <http://a/o> .",
  "<http://a/\\x> <http://a/p> <http://a/o> .",
  "<http://a/\\u00zz> <http://a/p> <http://a/o> .",
  "<http://a/\\U0000zzzz> <http://a/p> <http://a/o> .",
  "<http://a b> <http://a/p> <http://a/o> .",
  "<http://e/a|b^c`d> <http://a/p> <http://a/o> .",
  "<http://e/{a}> <http://a/p> <http://a/o> .",
  '<http://e/a"b> <http://a/p> <http://a/o> .',
  '<http://e/a"b\\u0041> <http://a/p> <http://a/o> .',
  "<http://a/s> <http://a/p> <",
  "<http://a/s> <http://a/p> <http://a/o",
  "<http://a/s> <http://a/p> >",
  "<http://a/s> <http://a/p> >> .",
  "<< <http://a/s> <http://a/p> <http://a/o> >> <http://a/p> <http://a/o> .",
  "<http://a/s> <http://a/p> << <http://a/s> <http://a/p> <http://a/o> >> .",
  "<http://a/s> <http://a/p> <http://a/o> {| <http://a/p> <http://a/o> |} .",
  "<http://a/s> <http://a/p> <http://a/o> |} .",
  "<http://a/s> <http://a/p> <http://a/o> | .",
  "<http://a/\u00e9\u{1f600}> <http://a/p> <http://a/\u2028> .",
  // Relative IRIs, with no base and with one
  "<rel> <p> <o> .",
  "<> <#f> <?q> .",
  "</a> <//h/a> <../x/./y> .",
  "<a/../../b/.> <./> <..> .",
  "<1a:b> <http://a/p> <http://a/o> .",
  "<http://a/s> <1a:b> <http://a/o> .",
  "<http://a/s> <http://a/p> <a:b:c> .",
  "@base <http://b/c/d?q#f> .\n<x> <../y> <./z/../w> .\n</r> <//h/p> <?k> .\n<#g> <> <a/./b/..> .",
  "@base <http://b/c/d?q#f> .\n<?a$&b$$c$'d$1> <a/../../../e> <.> .\n<..> <../..> <../../..> .",
  "@base <http://b/c/d/> .\n<..a/b> <a/..b/../c> <a/.../b> .\n</a/..> <a/../> <a//../b> .",
  "@base <http://b> .\n<x> </y/./z> <../w> .",
  "@base <http://b/c/> .\n<x/a:b/../c> <a/.b/../c> <a/../b?c/../d> .\n<a/../b#c/../d> <.b/./c> <a/..> .",
  "@base <urn:a:b> .\n<c/../d> <e/./f> <..> .",
  "@base <x:/a/b> .\n<../c> <./d> <../../../e> .",
  "@base <http://b/c/d;p?a/../b> .\n<x> <?y> <#z> .",
  "@base <file:///a/b/c> .\n<../../../d> </e/../f> <//g/h/../i> .",
  "@base <urn:a:b> .\n<c> </d> <//e> .",
  "@base <rel/path> .\n<x> </y> <//z> .",
  "@base <http://b/a\u2028?q> .\n<?r> <x> <.> .",
  "@base <http://b/c/> .\n@base <d/e> .\n<f> <g/..> <#h> .",
  "BASE <http://z/y/>\nPREFIX p: <q/>\np:a p:b p:c.",
  "base <http://z/y/> prefix p: <q/> p:a p:b p:c .",
  "BASE <http://z/y/> .",
  "@base <> .",
  "@base <1a:b> .",
  "@base :a .",
  "@base <http://b/>",
  "@base <http://b/> ;",
  "PREFIX p: <http://p/> @prefix q: <http://q/> . p:a q:b p:c .",
  "<http://h/a/../../../b> <x:/a/../b> <x:a/../b> .",
  "<http://h/a/b/c/../../d/./e/..> <http://h/a/..?x/../y> <http://h/a/..#x/../y> .",
  "<http://h/..> <http://h/.> <http://h/a/b/../..> .",
  // Blank nodes
  "_:a <http://a/p> _:b .\n_:a <http://a/p> _:a.b , _:c-d , _:9 , _:\u00e9 .",
  "_:a <http://a/p> _:b.",
  "_:a <http://a/p> _:b",
  "_: <http://a/p> _:b .",
  "_:.a <http://a/p> _:b .",
  "_x <http://a/p> _:b .",
  "_:a _:p _:b .",
  "[] <http://a/p> [] , [ ] .",
  "[ <http://a/p> <http://a/o> ] .",
  "[ <http://a/p> <http://a/o> ; <http://a/q> \"r\" , \"s\" ; ] <http://a/t> [ <http://a/u> [ ] ] .",
  "<http://a/s> [ <http://a/p> <http://a/o> ] <http://a/t> .",
  "<http://a/s> <http://a/p> [ <http://a/q> ] .",
  "<http://a/s> <http://a/p> [ <http://a/q> <http://a/o> <http://a/r> ] .",
  "<http://a/s> <http://a/p> [ <http://a/q> <http://a/o> . ] .",
  "<http://a/s> <http://a/p> [ <http://a/q> <http://a/o> ; . <http://a/x> <http://a/y> <http://a/z> ] .",
  "<http://a/s> <http://a/p> [ <http://a/q> <http://a/o> ; . <http://a/x> <http://a/y> <http://a/z> , \"w\" ; <http://a/v> 1 ] .",
  "[ <http://a/q> <http://a/o> ; . ] .",
  "[ <http://a/q> <http://a/o> ; . [ ] ] .",
  "<http://a/s> <http://a/p> [",
  "<http://a/s> <http://a/p> [ ] ]",
  "[ ] .",
  "[ ] <http://a/p> <http://a/o> .",
  "[ <http://a/p> <http://a/o> ] ; <http://a/q> <http://a/r> .",
  "[ <http://a/p> <http://a/o> ] }",
  "] <http://a/p> <http://a/o> .",
  // A bracket that closes nothing is refused and n3 reads on
  "<http://a/s> <http://a/p> <http://a/o> ; ] . <http://a/s> <http://a/p> <http://a/o> .",
  "<http://a/s> <http://a/p> <http://a/o> ; ] ] ] <http://a/q>",
  "<http://a/s> <http://a/p> <http://a/o> ; ] |} .",
  "<http://a/s> <http://a/p> <http://a/o> ; ] ; <http://a/q> <http://a/r> , \"t\" . @prefix : <1a:b> .",
  "<http://a/s> <http://a/p> ( <http://a/o> ) ; ] .",
  "<http://a/s> <http://a/p> ( ) ; ] <http://a/x> .",
  "<http://a/s> <http://a/p> ( [ <http://a/q> <http://a/o> ; ] ] ) .",
  "( <http://a/o> ; ] ) <http://a/p> <http://a/o> .",
  "<http://a/s> <http://a/p> <http://a/o> ; ] . [ <http://a/q> <http://a/r> ] <http://a/t> ( 1 \"a\"@en ) .",
  // Strings
  `${P}:a :p "" , "s" , 's' , '' , "it's" , 'say "x"' .`,
  `${P}:a :p "a\\tb\\nc\\rd\\be\\ff\\"g\\'h\\\\i" , 'a\\'b' .`,
  `${P}:a :p "\\u00e9\\U0001F600\\uD83D\\uDE00\\uD800" .`,
  `${P}:a :p "\\U00110000" , "\\UFFFFFFFF" , "\\U7FFFFFFF" , "\\U80000000" , "\\U0000FFFF" , "\\U00010000" .`,
  `${P}:a :p "\\_\\~\\.\\-\\!\\$\\&\\(\\)\\*\\+\\,\\;\\=\\/\\?\\#\\@\\%" .`,
  `${P}:a :p "\\q" .`,
  `${P}:a :p "\\u00e" .`,
  `${P}:a :p "a\\" .`,
  `${P}:a :p "a\\\\" .`,
  `${P}:a :p "a\\\\\\" .`,
  `${P}:a :p "a\\\\\\"b" .`,
  `${P}:a :p "unterminated .`,
  `${P}:a :p 'unterminated .`,
  `${P}:a :p "two\nlines" .`,
  `${P}:a :p 'two\rlines' .`,
  `${P}:a :p """long\n"q" ""two\\"""three\r\nlines""" , '''it''s\n''' , """""" , '''''' .\n:b :c :d :e`,
  `${P}:a :p """unterminated .`,
  `${P}:a :p """a\\q""" .`,
  `${P}:a :p ""`,
  `${P}:a :p "a"`,
  `${P}:a :p "a""b" .`,
  `${P}:a :p "a" "b" .`,
  `${P}:a :p "\u00e9\u{1f600}\u2028 \u00a0" .`,
  `${P}"s" :p :o .`,
  `${P}:a "p" :o .`,
  // Languages and datatypes
  `${P}:a :p "a"@en , "b"@EN-gb , "c"@en-Latn-GB , "d" @fr , "e"@x-1-y2 .`,
  `${P}:a :p "a"@en.`,
  `${P}:a :p "a"@en`,
  `${P}:a :p "a"@en- .`,
  `${P}:a :p "a"@1 .`,
  `${P}:a :p "a"@ .`,
  `${P}:a :p "a"@en@fr .`,
  `${P}:a :p "a"@en_GB .`,
  `${P}:a :p 1@en .`,
  `${P}:a :p "a"^^:t , "b"^^<http://t/> , "c"^^<http://www.w3.org/2001/XMLSchema#string> , "d"^^<> , "e"^^<rel> .`,
  `${P}:a :p "01"^^<http://www.w3.org/2001/XMLSchema#integer> , "TRUE"^^<http://www.w3.org/2001/XMLSchema#boolean> .`,
  `${P}:a :p "a"^^ :t .`,
  `${P}:a :p "a" ^^:t .`,
  `${P}:a :p "a"^^`,
  `${P}:a :p "a"^^x`,
  `${P}:a :p "a"^^1 .`,
  `${P}:a :p "a"^^"b" .`,
  `${P}:a :p "a"^^_:b .`,
  `${P}:a :p "a"^^<< .`,
  `${P}:a :p "a"^^q:t .`,
  `${P}:a :p "a"^^<1a:b> .`,
  `${P}:a :p "a"^^:t@en .`,
  `${P}:a :p ^^<http://t/> , ^^:t .`,
  `${P}^^:a ^^<http://a/p> :o .`,
  `${P}:a :p "a"^:t .`,
  `${P}:a :p "a"^ .`,
  `${P}:a ^:p :o .`,
  // Numbers and booleans
  `${P}:a :p 1 , 01 , +1 , -1 , 1.5 , .5 , -.5 , 1e0 , 1E5 , 1.e3 , 1.5e-3 , +.5E+3 , 12345678901234567890 , 0.0 .`,
  `${P}:a :p 1.`,
  `${P}:a :p 1`,
  `${P}:a :p 1.5.`,
  `${P}:a :p .5.`,
  `${P}:a :p 1e5.`,
  `${P}:a :p 1 .5 , 2. .`,
  `${P}:a :p 1e .`,
  `${P}:a :p --1 .`,
  `${P}:a :p 1.2.3 .`,
  `${P}:a :p 1a .`,
  `${P}:a :p + .`,
  `${P}:a :p 1.e .`,
  `${P}:a :p 1:b .`,
  `${P}:a :p true , false .`,
  `${P}:a :p true.`,
  `${P}:a :p True .`,
  `${P}:a :p truex .`,
  `${P}:a :p false1 .`,
  `${P}@prefix t: <http://t/> .\n@prefix f: <http://f/> .\nt:rue f:alse true:x .`,
  `${P}1 :p :o .`,
  `${P}true :p :o .`,
  `${P}:a 1 :o .`,
  // Keywords and declarations
  "@prefix p: <http://p/> .\n@prefix : <http://e/> .\n@prefix p.q-r_s: <http://pq/> .\np:a :b p.q-r_s:c .",
  "@prefix p:<http://p/>.p:a p:b p:c.",
  "@prefix p:#c\n<http://p/> .\np:a p:b p:c .",
  "@prefix p <http://p/> .",
  "@prefix <http://p/> .",
  "@prefix p: :a .",
  "@prefix p: \"a\" .",
  "@prefix p: <http://p/>",
  "@prefix p: <http://p/> ;",
  "@prefix p: <http://p/> @prefix q: <http://q/> .",
  "@prefix p: <rel/> .\np:a p:b p:c .",
  "@prefix p: <1a:b> .",
  "@prefix : <1a:b> . <http://a/s> <http://a/p> <http://a/o> ; ] .",
  "@prefix \u00e9\u{10000}: <http://p/> .\n\u00e9\u{10000}:a \u00e9\u{10000}:\u00e9\u{10000} \u00e9\u{10000}:c .",
  "@prefix _: <http://p/> .",
  "@prefix 1: <http://p/> .",
  "@PREFIX p: <http://p/> .",
  "@Prefix p: <http://p/> .",
  "@a <http://a/p> <http://a/o> .",
  "@ <http://a/p> <http://a/o> .",
  "@forSome <http://a/p> .",
  "@forAll <http://a/p> .",
  "<http://a/s> @forAll <http://a/p> .",
  "<http://a/s> <http://a/p> @base .",
  "PREFIX p: <http://p/>\nprefix q: <http://q/>\nPrEfIx r: <http://r/>\np:a q:b r:c .",
  "PREFIX p: <http://p/> .",
  "PREFIX p <http://p/>",
  "PREFIX: <http://p/> :a :b :c .",
  "GRAPH <http://g/> { <http://a/s> <http://a/p> <http://a/o> }",
  "graph <http://g/> { }",
  "<http://a/s> <http://a/p> GRAPH .",
  "@prefix G: <http://g/> .\n@prefix b: <http://b/> .\n@prefix P: <http://p/> .\nG:a b:ase P:refix .\nGRAPHS:a b:b P:c .",
  "@prefix : <http://e/> .\n@prefix : <http://f/> .\n:a :b :c .",
  // Prefixed names
  `${P}:a :b :c.d , :c.d.e , :9x , :a-b_c , :%41 , :a%4a , :x:y , :: .`,
  `${P}:a :b :c\\.d , :\\~ , :a\\-\\_\\!\\$\\&\\'\\(\\)\\*\\+\\,\\;\\=\\/\\?\\#\\@\\% .`,
  `${P}:a :b :c.`,
  `${P}:a :b :c`,
  `${P}:a :b :c..`,
  `${P}:a :b :`,
  `${P}: : : .`,
  `${P}:a :b :c,:d;:e :f.`,
  `${P}:a :b q:c .`,
  `${P}q:a :b :c .`,
  `${P}:a q:b :c .`,
  `${P}:a :b :c\\q .`,
  `${P}:a :b :%4 .`,
  `${P}:a :b :c! .`,
  `${P}:a :b :c^:d .`,
  `${P}:a :b :\u00e9\u{1f600}\u00b7\u203f .`,
  `${P}:a :b :\u00b7 .`,
  `${P}:a :b :\u017f , :\u212a .`,
  `${P}:a :b abc .`,
  `${P}:a :b a:b .`,
  `${P}a :b :c .`,
  `${P}:a a :c ; a :d , :e .`,
  `${P}:a a`,
  `${P}:a :b a .`,
  // Characters that begin nothing in Turtle
  `${P}:a :b ?x .`,
  `${P}?x :b :c .`,
  `${P}:a = :c .`,
  `${P}:a => :c .`,
  `${P}:a!:b :c :d .`,
  `${P}:a :b :c !`,
  `${P}:a :b \\x .`,
  `${P}:a :b * .`,
  `${P}:a :b \u{1f600} .`,
  `${P}:a :b { :c :d :e } .`,
  `${P}{ :a :b :c }`,
  `${P}:a :b :c }`,
  `${P}}`,
  `${P}:a }`,
  `${P}:a :b :c ; }`,
  `${P}:a :b {`,
  `${P}:a {`,
  `${P}:a :b {| :c :d |} .`,
  `${P}:a :b :c {| :d :e |} .`,
  `${P}:a :b :c {`,
  `${P}:a :b :c |`,
  `${P}:a :b :c |}`,
  // Statements
  `${P}:a :b :c ; :d :e , :f ; ; ;; :g :h ; .`,
  `${P}:a :b :c ;`,
  `${P}:a :b :c ,`,
  `${P}:a :b :c , ;`,
  `${P}:a ; :b :c .`,
  `${P}:a :b ; :c .`,
  `${P}:a :b , :c .`,
  `${P}:a :b :c :d .`,
  `${P}:a :b "c" :d .`,
  `${P}:a :b "c"@en :d .`,
  `${P}:a :b "c"^^:t :d .`,
  `${P}:a :b 1 :d .`,
  `${P}:a :b _:c :d .`,
  `${P}:a :b [ ] :d .`,
  `${P}:a :b ( ) :d .`,
  `${P}:a :b ( 1 ) :d .`,
  `${P}:a :b "say \\"x\\"" :d .`,
  `${P}:a .`,
  `${P}:a :b .`,
  `${P}. :a :b :c .`,
  `${P}:a :b :c . .`,
  `${P}, :a :b :c .`,
  `${P}:a :b :c`,
  `${P}:a :b`,
  `${P}:a`,
  `${P}:a [ :b :c ] :d .`,
  `${P}:a _:b :c .`,
  `${P}:a ( ) :c .`,
  `${P}:a "b" :c .`,
  // Lists
  `${P}:a :p ( ) , ( :b ) , ( :b "c" 3 true "d"@en "e"^^:t 1.5 ) .`,
  `${P}:a :p ( ( ) ( :b ) ( ( :c ) ) ) .`,
  `${P}:a :p ( [ ] [ :q :r ] [ :q ( :s ) ; :t "u" ] ) .`,
  `${P}( ) :p :o .`,
  `${P}( :a :b ) :p :o ; :q ( :c ) .`,
  `${P}( ( :a ) ) :p :o .`,
  `${P}( [ :q :r ] ) :p ( _:x _:y ) .`,
  `${P}( :a ) .`,
  `${P}( ) .`,
  `${P}:a :p ( :b`,
  `${P}:a :p ( :b .`,
  `${P}:a :p ( :b , :c ) .`,
  `${P}:a :p ( :b ; ) .`,
  `${P}:a :p ( "b"`,
  `${P}:a :p ( "b"^^q:t ) .`,
  `${P}:a :p ( "b"@en "c"^^<http://t/> ^^:t ) .`,
  `${P}:a :p ( { } ) .`,
  `${P}:a :p ( << ) .`,
  `${P}:a :p ( q:b ) .`,
  `${P}:a :p ( a ) .`,
  `${P}:a :p ) .`,
  `${P}) :p :o .`,
  `${P}:a :p ( :b ] ) .`,
  `${P}:a :p [ :q ( :b ] ) .`,
  `${P}:a :p ( [ :q :r ) ] .`,
  `${P}:a :p ( :b ) ) .`,
  `${P}:a :p ( <http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> ) , <http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> .`,
  `${P}<http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> :p ( ) .`,
  // What a vocabulary file looks like, and the reviewed differences from another parser
  `${P}${S}:a a skos:Concept ; skos:prefLabel "a label"@en , "b"@EN-gb ; skos:broader :b ; skos:notation 01 , 1e0 , 1.e3 .`,
  `${P}${S}:a @a skos:Concept .`,
  `${P}${S}:a!skos:broader skos:note "x" .`,
  `${P}${S}"lit" skos:note "x" .`,
  `${P}${S}true skos:note "x" .`,
  `${P}${S}?x skos:prefLabel "v" .`,
  `${P}${S}[] skos:inScheme <http://e/> ; skos:prefLabel "b" .\n_:q skos:inScheme <http://e/> .`,
  `${P}${S}:a skos:member ( :b [ skos:prefLabel "c" ] ( :d ) ) , [ skos:related [ skos:note ( ) ] ] .`,
];

// A generator with a stated seed, so the damaged documents are the same on every run.
function generator(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const HEAD = `${P}${S}`;
const bases = [
  `${HEAD}:a a skos:Concept ; skos:prefLabel "a label"@en , "b"@EN-gb ; skos:broader :b .\n:b :p 01 , 1.5 , 1e0 , -.5e-3 , true , false , "x"^^:t , "y"^^<http://t/> .\n`,
  `${HEAD}:a :p [ :q :r ; :s "t" ] , ( :x "y" 3 ( ) ( :z ) [ ] ) .\n[ :p :o ] :q :r .\n( :a :b ) :p :o .\n[] :p _:b1 . _:b1 :q _:b.2 .\n`,
  `${HEAD}@base <http://b/c/d?q#f> .\n<x> <../y> <./z/../w> . </r> <//h/p> <?k> . <#g> <> <a/./b/..> .\nBASE <http://z/>\nPREFIX p: <q/>\np:a p:b p:c.\n`,
  `${HEAD}:a :p """long\n"q" \\" text""" , 's' , '''t\n''' , "e\\n\\t\\u00e9\\U0001F600\\\\" .\n<http://e/\\u00e9> :p :a\\.b , :c%41 , :d\\~e , :9x , :a.b.c .\n`,
  `# comment\n<http://a/s> <http://a/p> <http://a/o> . # more\n<http://a/s> <http://a/p> "l" .`,
  `${HEAD}:a :b :c ; ; :d :e ; .\n:f :g :h , :i ; :j [ ] ; :k ( ) .\n`,
  `${HEAD}:a :p "\u{1f600}" , "\\uD83D\\uDE00" , :\u{10000}x . :\u00e9 :p "\u00e9"@fr-CA .\n`,
];
// Text put in at a random place: marks, spellings n3 treats specially, and characters that
// JavaScript and Python patterns class differently.
const inserts = [
  ..." \n\t.;,[]()<>\"'@^_:#\\a1e+-?!{}|%uU/&$=~b0E",
  '"""', "'''", "^^", "_:", "<<", ">>", "{|", "|}", "@prefix", "@base", "PREFIX", "BASE", "GRAPH", "true", "false",
  ":a", "<http://x/>", '"s"', "@en", "..", "\r", "\u00a0", "\ufeff", "\u2028", "\u{1f600}", "\\u0041", "\\U00110000",
  "\\n", "\u000b", "\u000c", "\u001c", "\u0085", "\u212a", "\u017f", "\u00e9", "<>", "</a>", "<?q$&$'$$>", "<a:b>",
  "<1a:b>", "<../x>", "@forSome", "@forAll", "@a", "1.", ".5", "1e", "--1", " a ", "[]", "()",
];
// Whole tokens, so that most of a sequence gets past the lexer and into the parser's states.
const tokens = [
  ":a", ":b", ":c", "<http://x/y>", '"s"', '"s"@en', '"1"^^:t', '"1"^^<http://t/>', "1", "1.5", "true", "a",
  ".", ".", ".", ";", ";", ",", ",", "[", "]", "[", "]", "(", ")", "(", ")", "_:b", "[]", "()",
  "@prefix p: <http://p/> .", "PREFIX p: <http://p/>", "@base <http://b/c/d> .", "BASE <x/>", "^^<http://t/>",
  "^^:t", "@en", "{", "}", "{|", "|}", "<<", ">>", "GRAPH", "@forAll", "p:x", "<rel>", "</abs>", "<//s/t>",
  "<?q>", "<#f>", "<>", "<../a/./b>", "<a/../../b/.>", "'t'", '"""u"""', "<1a:b>", "@prefix : <1a:b> .",
  "@base <> .", "BASE <http://h?x#y>", "<a/b/c/../../d/./e/..>", "</a/..>", "<..>",
];

function damaged(text, next) {
  const pick = (items) => items[Math.floor(next() * items.length)];
  let out = text;
  for (let step = pick([1, 1, 1, 2, 2, 3, 5]); step > 0; step -= 1) {
    const at = Math.floor(next() * (out.length + 1));
    const kind = Math.floor(next() * 5);
    if (kind === 0) out = out.slice(0, at) + out.slice(at + pick([1, 1, 1, 2, 5]));
    else if (kind === 1) out = out.slice(0, at) + pick(inserts) + out.slice(at);
    else if (kind === 2) {
      const [from, to] = [at, Math.floor(next() * (out.length + 1))].sort((a, b) => a - b);
      out = out.slice(0, from) + out.slice(to) + out.slice(from, to).slice(0, 20);
    } else if (kind === 3) {
      const to = Math.min(out.length, at + Math.floor(next() * 12));
      out = out.slice(0, to) + out.slice(at, to) + out.slice(to);
    } else out = out.slice(0, at);
  }
  return out;
}

export const mutationSeed = 20261121;
export function mutations(count) {
  const next = generator(mutationSeed);
  const pick = (items) => items[Math.floor(next() * items.length)];
  const out = [];
  while (out.length < count) {
    const roll = next();
    let text;
    if (roll < 0.5) text = HEAD + Array.from({ length: 1 + Math.floor(next() * 15) }, () => pick(tokens)).join(" ");
    else if (roll < 0.9) text = damaged(pick(bases), next);
    else text = (next() < 0.3 ? "" : HEAD) + Array.from({ length: 1 + Math.floor(next() * 13) }, () => pick(inserts) + pick(["", " ", " ", "\n"])).join("");
    // A document is text read from a file, which never holds half of a surrogate pair.
    if (text.isWellFormed()) out.push(text);
  }
  return out;
}

// Byte strings that are not all UTF-8, and a few that are: Node puts U+FFFD where it cannot
// decode, and how many it puts for one bad sequence is the thing recorded.
export function byteStrings(count) {
  const next = generator(mutationSeed + 1);
  const fixed = [
    "", "61", "c3a9", "f09f9880", "efbbbf61", "ff", "fffe", "c3", "c328", "e282", "e28228", "f09f98", "f09f9828",
    "eda080", "edb080", "eda080edb080", "c080", "c1bf", "e08080", "f0808080", "f4908080", "f8888080", "fc84808080",
    "80", "bf", "61ff62", "e2828061e282", "f09f61", "efbfbd", "efbfbe", "efbfbf", "00", "7f", "c280", "dfbf", "e0a080",
  ];
  const pieces = ["61", "c3a9", "e282ac", "f09f9880", "ff", "c3", "e2", "e282", "f0", "f09f", "f09f98", "80", "bf", "ed", "a0", "c0", "f4", "90"];
  const out = [...fixed];
  while (out.length < count)
    out.push(Array.from({ length: 1 + Math.floor(next() * 6) }, () => pieces[Math.floor(next() * pieces.length)]).join(""));
  return out;
}

const plainTypes = new Set(["http://www.w3.org/2001/XMLSchema#string", "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"]);
// One term on a line: its kind and its value, and for a literal its language and datatype,
// leaving out the two datatypes every plain literal has.
export const term = (node) =>
  node.termType === "Literal"
    ? `Literal ${JSON.stringify(node.value)} ${node.language} ${plainTypes.has(node.datatype.value) ? "" : node.datatype.value}`
    : `${node.termType} ${node.value}`;
export const quadLine = (quad) => `${term(quad.subject)}\t${quad.predicate.value}\t${term(quad.object)}\n`;

// Where n3's two blank node counters stand for the next document a parser reads. Both are
// global to the process, so they are read by parsing a statement that shows them, which
// itself moves each on by one.
export function blankLabels(Parser) {
  const [quad] = new Parser({ format: "Turtle" }).parse("_:q <p:a> [] .");
  return {
    documents: Number(/^b(\d+)_q$/.exec(quad.subject.value)[1]) + 1,
    anonymous: Number(/^n3-(\d+)$/.exec(quad.object.value)[1]) + 1,
  };
}

function read(Parser, text) {
  const labels = blankLabels(Parser);
  try {
    return { text, labels, error: null, lines: new Parser({ format: "Turtle" }).parse(text).map(quadLine) };
  } catch (error) {
    return { text, labels, error: error.message, lines: null };
  }
}

export function recordTurtle(Parser, sha256) {
  const damagedDocuments = mutations(600).map((text) => {
    const { labels, error, lines } = read(Parser, text);
    // The statements of a damaged document are kept as a count and a digest.
    return { text, labels, error, statements: lines?.length ?? null, lines_sha256: lines ? sha256(lines.join("")) : null };
  });
  return {
    documents: documents.map((text) => read(Parser, text)),
    mutation_seed: mutationSeed,
    mutations: damagedDocuments,
    decoding: byteStrings(240).map((hex) => ({ hex, text: Buffer.from(hex, "hex").toString("utf8") })),
  };
}
