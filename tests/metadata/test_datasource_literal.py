from opendp_whitenoise.metadata.collection import *
import copy

class TestDSMetadataLiteral:
    def test_create_ds_literal(self):
        table1 = Table("dbo", "d1", 5000, \
            [\
                String("DeviceID", 0, True),\
                Boolean("Refurbished"), \
                Float("Temperature", 20.0, 70.0)
            ])

        table2 = copy.copy(table1)
        table2.name = "d2"
        x = CollectionMetadata([table1],"csv")
        y = CollectionMetadata([table2],"csv")
        assert(x["dbo.d1"].name != y["dbo.d2"].name)
