import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from service.rpc import validate_read,ReadRefused
class ProfileReadTests(unittest.TestCase):
 def test_bounded_address_validation_only(self):
  validate_read('validateaddress',['qb1z'+'a'*58])
  for params in ([],['qb1z'+'a'*1000],['http://host'],['qb1z'+'a'*58,True]):
   with self.assertRaises(ReadRefused):validate_read('validateaddress',params)
 def test_malformed_proofs_and_spending_still_refused(self):
  for method,params in [('verifydatapqchash',[{}]),('verifydatapqchash',[{'proof_mode':'other'}]),('sendrawtransaction',['00']),('signmessage',[])]:
   with self.assertRaises(ReadRefused):validate_read(method,params)
